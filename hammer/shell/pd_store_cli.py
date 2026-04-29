"""
hammer-pd-store: tiny CLI for the Postgres PD artifact store POC.

Subcommands:
    init              Create the hammer_poc schema + pd_artifacts table (idempotent).
    list [-n N]       List the N most recent artifacts.
    get  <sha256>     Print the JSON payload for the given SHA256 to stdout.
    put  <path>       Store a JSON file as a par-input artifact; prints its SHA256.

Connection settings come from the HAMMER_PG_* environment variables; see
hammer/vlsi/pd_store.py for the full list.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

from hammer.config import HammerJSONEncoder
from hammer.vlsi import pd_store


def _cmd_init(_args: argparse.Namespace) -> int:
    pd_store.ensure_schema()
    print(f"Initialized schema '{pd_store.SCHEMA_NAME}' and table '{pd_store.TABLE_NAME}'.")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = pd_store.list_artifacts(limit=args.limit)
    if not rows:
        print("(no artifacts)")
        return 0
    print(f"{'sha256':<66} {'kind':<12} {'top_module':<24} created_at")
    print("-" * 130)
    for sha256, kind, top_module, created_at in rows:
        tm = top_module or "-"
        print(f"{sha256:<66} {kind:<12} {tm:<24} {created_at}")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    data = pd_store.load_artifact(args.sha256)
    if data is None:
        print(f"No artifact found with sha256={args.sha256}", file=sys.stderr)
        return 1
    print(json.dumps(data, cls=HammerJSONEncoder, indent=4))
    return 0


def _cmd_put(args: argparse.Namespace) -> int:
    with open(args.path, "r") as f:
        data = json.load(f)
    sha = pd_store.store_artifact(data, kind=args.kind)
    print(sha)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hammer-pd-store",
        description="Postgres PD artifact store (POC).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create schema + table if not present.")
    p_init.set_defaults(func=_cmd_init)

    p_list = sub.add_parser("list", help="List recent artifacts.")
    p_list.add_argument("-n", "--limit", type=int, default=20,
                        help="Number of rows to show (default: 20).")
    p_list.set_defaults(func=_cmd_list)

    p_get = sub.add_parser("get", help="Fetch an artifact by SHA256 and print its JSON.")
    p_get.add_argument("sha256", help="SHA256 hex digest of the artifact.")
    p_get.set_defaults(func=_cmd_get)

    p_put = sub.add_parser("put", help="Store a JSON file as an artifact.")
    p_put.add_argument("path", help="Path to the JSON file to store.")
    p_put.add_argument("--kind", default="par-input",
                       help="Artifact kind label (default: par-input).")
    p_put.set_defaults(func=_cmd_put)

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
