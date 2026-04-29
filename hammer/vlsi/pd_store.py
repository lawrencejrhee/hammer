"""
Proof-of-concept Postgres-backed store for PD artifacts.

This module provides a minimal round-trip write/read path for Hammer
PD artifacts (currently just ``par-input.json``) into Postgres. Content
is keyed by the SHA256 of its canonical JSON representation, so identical
inputs deduplicate naturally.

Connection settings are resolved with this precedence (first hit wins per
field):

    1. HAMMER_PG_* environment variables
         HAMMER_PG_HOST / HAMMER_PG_PORT / HAMMER_PG_DB /
         HAMMER_PG_USER / HAMMER_PG_PASSWORD
    2. ``sql_alchemy_conn`` from ``airflow.cfg``
         (the same connection string Airflow uses for its metadata DB)
    3. Hardcoded defaults
         host=barney.eecs.berkeley.edu, port=5433,
         db=airflow_lawrence, user=$USER, password=<none>

So if your ``airflow.cfg`` already contains a valid
``sql_alchemy_conn = postgresql+psycopg2://user:pass@host:port/db`` line,
``pd_store`` will use it automatically with no env vars required.

Which ``airflow.cfg`` is used:
    ``$AIRFLOW_HOME/airflow.cfg`` if AIRFLOW_HOME is set, else
    ``<cwd>/airflow.cfg`` if present, else
    ``~/airflow/airflow.cfg``.

All data lives in the ``hammer_poc`` schema so it stays isolated from
Airflow's own tables.
"""

from __future__ import annotations

import configparser
import getpass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import psycopg2
from psycopg2.extras import Json

from hammer.config import HammerJSONEncoder

__all__ = [
    "store_par_input",
    "store_artifact",
    "load_artifact",
    "list_artifacts",
    "ensure_schema",
    "compute_sha256",
]


SCHEMA_NAME = "hammer_poc"
TABLE_NAME = "pd_artifacts"
FQ_TABLE = f"{SCHEMA_NAME}.{TABLE_NAME}"


def _find_airflow_cfg() -> Optional[Path]:
    """Locate an ``airflow.cfg`` to use as a fallback settings source."""
    candidates = []
    if os.environ.get("AIRFLOW_HOME"):
        candidates.append(Path(os.environ["AIRFLOW_HOME"]) / "airflow.cfg")
    candidates.append(Path.cwd() / "airflow.cfg")
    candidates.append(Path.home() / "airflow" / "airflow.cfg")
    for p in candidates:
        if p.is_file():
            return p
    return None


def _parse_airflow_cfg_conn() -> Dict[str, Any]:
    """
    Pull host/port/db/user/password from ``sql_alchemy_conn`` in airflow.cfg.

    Returns an empty dict on any failure - this is a best-effort fallback,
    not a required source.
    """
    cfg_path = _find_airflow_cfg()
    if cfg_path is None:
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(cfg_path)
    except Exception:
        return {}
    if not parser.has_option("database", "sql_alchemy_conn"):
        return {}
    conn_str = parser.get("database", "sql_alchemy_conn")
    # Strip SQLAlchemy driver prefix (e.g. "postgresql+psycopg2://") so
    # urlparse sees a recognized scheme.
    if "+" in conn_str.split("://", 1)[0]:
        scheme, rest = conn_str.split("://", 1)
        conn_str = scheme.split("+", 1)[0] + "://" + rest
    try:
        url = urlparse(conn_str)
    except Exception:
        return {}
    if url.scheme not in ("postgres", "postgresql"):
        return {}
    out: Dict[str, Any] = {}
    if url.hostname:
        out["host"] = url.hostname
    if url.port:
        out["port"] = url.port
    if url.username:
        out["user"] = unquote(url.username)
    if url.password:
        out["password"] = unquote(url.password)
    dbname = url.path.lstrip("/") if url.path else ""
    if dbname:
        out["dbname"] = dbname
    return out


def _pg_settings() -> Dict[str, Any]:
    """
    Gather Postgres connection settings.

    Precedence (first hit wins per field):
        1. HAMMER_PG_* environment variables
        2. sql_alchemy_conn from airflow.cfg
        3. Hardcoded defaults
    """
    cfg = _parse_airflow_cfg_conn()
    try:
        default_user = getpass.getuser()
    except Exception:
        default_user = "postgres"

    host = (
        os.environ.get("HAMMER_PG_HOST")
        or cfg.get("host")
        or "barney.eecs.berkeley.edu"
    )
    port = int(
        os.environ.get("HAMMER_PG_PORT")
        or cfg.get("port")
        or 5433
    )
    dbname = (
        os.environ.get("HAMMER_PG_DB")
        or cfg.get("dbname")
        or "airflow_lawrence"
    )
    user = (
        os.environ.get("HAMMER_PG_USER")
        or cfg.get("user")
        or default_user
    )
    password = (
        os.environ.get("HAMMER_PG_PASSWORD")
        or cfg.get("password")
    )
    if not password:
        raise RuntimeError(
            "No Postgres password found. Set HAMMER_PG_PASSWORD in the "
            "environment, or ensure airflow.cfg's sql_alchemy_conn "
            "contains a password."
        )
    return {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
    }


def _connect():
    """Open a new psycopg2 connection using env-var config."""
    return psycopg2.connect(**_pg_settings())


_DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME};

CREATE TABLE IF NOT EXISTS {FQ_TABLE} (
    sha256      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    top_module  TEXT,
    data        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _ensure_schema(conn) -> None:
    """Create the schema + table if they don't exist. Safe to call repeatedly."""
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()


def ensure_schema() -> None:
    """Public entry point to create the schema + table (idempotent)."""
    conn = _connect()
    try:
        _ensure_schema(conn)
    finally:
        conn.close()


def _canonical_json(data: Dict[str, Any]) -> str:
    """
    Produce a deterministic JSON string for hashing.

    Uses sorted keys and no whitespace so logically equal dicts
    produce identical bytes across runs/machines.
    """
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        cls=HammerJSONEncoder,
        ensure_ascii=False,
    )


def compute_sha256(data: Dict[str, Any]) -> str:
    """Return the hex SHA256 of the canonical JSON form of ``data``."""
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _extract_top_module(data: Dict[str, Any]) -> Optional[str]:
    """Best-effort extraction of the top module name for convenience."""
    for key in ("synthesis.inputs.top_module", "par.inputs.top_module", "vlsi.inputs.top_module"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def store_artifact(data: Dict[str, Any], kind: str) -> str:
    """
    Store ``data`` as an artifact of the given ``kind`` and return its SHA256 hex.

    Uses INSERT ... ON CONFLICT DO NOTHING so identical content is deduplicated
    and repeated calls are idempotent.
    """
    sha = compute_sha256(data)
    top_module = _extract_top_module(data)
    conn = _connect()
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {FQ_TABLE} (sha256, kind, top_module, data)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sha256) DO NOTHING
                """,
                (sha, kind, top_module, Json(data)),
            )
        conn.commit()
    finally:
        conn.close()
    return sha


def store_par_input(data: Dict[str, Any]) -> str:
    """Convenience wrapper: store a par-input dict and return its SHA256 hex."""
    return store_artifact(data, kind="par-input")


def load_artifact(sha256: str) -> Optional[Dict[str, Any]]:
    """Fetch an artifact by SHA256. Returns the JSON payload as a dict, or None."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT data FROM {FQ_TABLE} WHERE sha256 = %s",
                (sha256,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return row[0]


def list_artifacts(limit: int = 20) -> List[Tuple[str, str, Optional[str], Any]]:
    """
    List the most recent artifacts.

    Returns tuples of (sha256, kind, top_module, created_at).
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT sha256, kind, top_module, created_at
                FROM {FQ_TABLE}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()
