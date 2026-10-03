import types

import pytest

from hammer.vlsi import pd_store


class _LockNotAvailable(Exception):
    pass


class _InsufficientPrivilege(Exception):
    pass


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        if pd_store._DDL in sql and self.conn.ddl_error is not None:
            raise self.conn.ddl_error

    def fetchone(self):
        return (self.conn.stamp,)


class _Conn:
    def __init__(self, stamp=None, ddl_error=None, dbname="db"):
        self.stamp, self.ddl_error = stamp, ddl_error
        self.info = types.SimpleNamespace(host="h", port=5433, dbname=dbname)
        self.autocommit = False
        self.sql, self.commits, self.rollbacks = [], 0, 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def ddl_runs(self):
        return [s for s in self.sql if pd_store._DDL in s]


@pytest.fixture(autouse=True)
def _fresh_process(monkeypatch):
    monkeypatch.setattr(pd_store, "_schema_settled", set())
    errors = types.SimpleNamespace(LockNotAvailable=_LockNotAvailable, InsufficientPrivilege=_InsufficientPrivilege)
    monkeypatch.setattr(pd_store, "psycopg2", types.SimpleNamespace(errors=errors))


def test_hot_path_skips_ddl_when_version_matches() -> None:
    conn = _Conn(stamp=pd_store._DDL_VERSION)
    pd_store._ensure_schema(conn, quiet=True)
    assert conn.ddl_runs() == []


def test_hot_path_runs_ddl_once_on_mismatch() -> None:
    conn = _Conn(stamp="sledgehammer:old")
    pd_store._ensure_schema(conn, quiet=True)
    pd_store._ensure_schema(conn, quiet=True)
    assert len(conn.ddl_runs()) == 1
    assert conn.ddl_runs()[0].endswith(f"COMMENT ON TABLE {pd_store.FQ_CHECKPOINT} IS '{pd_store._DDL_VERSION}';\n")


def test_each_database_is_checked() -> None:
    pd_store._ensure_schema(_Conn(stamp=pd_store._DDL_VERSION, dbname="a"), quiet=True)
    other = _Conn(stamp="sledgehammer:old", dbname="b")
    pd_store._ensure_schema(other, quiet=True)
    assert len(other.ddl_runs()) == 1


def test_init_always_runs_ddl() -> None:
    conn = _Conn(stamp=pd_store._DDL_VERSION)
    pd_store._ensure_schema(conn, quiet=True)
    pd_store._ensure_schema(conn)
    assert len(conn.ddl_runs()) == 1
    assert not conn.ddl_runs()[0].startswith("SET LOCAL")


def test_lock_timeout_rides_with_ddl_and_is_swallowed() -> None:
    conn = _Conn(stamp=None, ddl_error=_LockNotAvailable())
    conn.autocommit = True
    pd_store._ensure_schema(conn, quiet=True)
    assert conn.rollbacks == 1
    assert len(conn.ddl_runs()) == 1
    assert conn.ddl_runs()[0].startswith("SET LOCAL lock_timeout = '2s';\n")
    pd_store._ensure_schema(conn, quiet=True)
    assert len(conn.ddl_runs()) == 2


def test_non_owner_tries_once_per_process() -> None:
    conn = _Conn(stamp="sledgehammer:old", ddl_error=_InsufficientPrivilege())
    pd_store._ensure_schema(conn, quiet=True)
    pd_store._ensure_schema(conn, quiet=True)
    assert len(conn.ddl_runs()) == 1 and conn.rollbacks == 1


def test_init_still_raises_for_a_non_owner() -> None:
    with pytest.raises(_InsufficientPrivilege):
        pd_store._ensure_schema(_Conn(ddl_error=_InsufficientPrivilege()))


def test_version_follows_the_ddl_text() -> None:
    assert pd_store._DDL_VERSION.startswith("sledgehammer:")
    assert pd_store._DDL_VERSIONED.startswith(pd_store._DDL)
    assert pd_store._DDL_VERSION[len("sledgehammer:"):] in pd_store._DDL_VERSIONED
