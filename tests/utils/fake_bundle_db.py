"""The design-bundle tables of the shared database in memory, as psycopg2 shows them to each user."""
import datetime
import re
import types

from hammer.vlsi import pd_store


class FakeBundleDB:
    """Rows show to their owner and to those the design is shared with; a REPEATABLE READ connection reads the
    snapshot taken at its first statement, any other connection the live tables."""

    def __init__(self) -> None:
        self.user = "alice"
        self.bundles: dict = {}
        self.chunks: dict = {}
        self.shares: set = set()
        self.statements: list = []
        self.named: list = []
        self.after_bundle_read = None

    def install(self, monkeypatch) -> "FakeBundleDB":
        driver = types.SimpleNamespace(Binary=lambda b: b, Error=Exception,
                                       OperationalError=type("OperationalError", (Exception,), {}))
        monkeypatch.setattr(pd_store, "psycopg2", driver)
        monkeypatch.setattr(pd_store, "Json", lambda value: value)
        monkeypatch.setattr(pd_store, "_connect", lambda: FakeConn(self, self.user))
        monkeypatch.setattr(pd_store, "_ensure_schema", lambda conn, quiet=False: None)
        return self


class FakeConn:
    def __init__(self, db: FakeBundleDB, user: str) -> None:
        self.db, self.user = db, user
        self.repeatable = self.readonly = False
        self.snapshot = None

    def set_session(self, isolation_level=None, readonly=None, **_) -> None:
        assert self.snapshot is None, "set_session inside a transaction"
        self.repeatable = str(isolation_level).upper() in ("REPEATABLE READ", "SERIALIZABLE")
        self.readonly = bool(readonly)

    def tables(self) -> tuple:
        live = (self.db.bundles, self.db.chunks, self.db.shares)
        if not self.repeatable:
            return live
        if self.snapshot is None:
            self.snapshot = (dict(self.db.bundles), dict(self.db.chunks), set(self.db.shares))
        return self.snapshot

    def visible(self, owner: str, design: str, shares: set) -> bool:
        return owner == self.user or (owner, design, self.user) in shares

    def cursor(self, name=None) -> "FakeCursor":
        return FakeCursor(self, name)

    def commit(self) -> None:
        self.snapshot = None

    def rollback(self) -> None:
        self.snapshot = None

    def close(self) -> None:
        self.snapshot = None


class FakeCursor:
    def __init__(self, conn: FakeConn, name) -> None:
        self.conn, self.name, self.rows, self.itersize = conn, name, [], 2000

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params=()) -> None:
        conn, db, flat = self.conn, self.conn.db, " ".join(sql.split())
        db.statements.append((flat, params))
        bundles, chunks, shares = conn.tables()
        if flat.startswith(("INSERT", "DELETE", "UPDATE")):
            assert not conn.readonly, "a write in a read-only transaction"
        if flat.startswith(f"SELECT bundle_sha FROM {pd_store.FQ_DESIGN_BUNDLE} WHERE owner = current_user"):
            row = bundles.get((conn.user, params[0]))
            self.rows = [(row["bundle_sha"],)] if row else []
        elif flat.startswith(f"INSERT INTO {pd_store.FQ_DESIGN_BUNDLE} "):
            design, bundle_sha, manifest, data, size = params
            db.bundles[(conn.user, design)] = dict(
                bundle_sha=bundle_sha, manifest=manifest, data=data, size_bytes=size,
                updated_at=datetime.datetime(2026, 10, 28, tzinfo=datetime.timezone.utc))
        elif flat.startswith(f"DELETE FROM {pd_store.FQ_DESIGN_BUNDLE_CHUNK} WHERE owner = current_user"):
            db.chunks = {k: v for k, v in db.chunks.items() if k[:2] != (conn.user, params[0])}
        elif flat.startswith(f"INSERT INTO {pd_store.FQ_DESIGN_BUNDLE_CHUNK} "):
            db.chunks[(conn.user, params[0], params[1])] = params[2]
        elif re.match(rf"SELECT [\w, ]+ FROM {re.escape(pd_store.FQ_DESIGN_BUNDLE)} WHERE owner = %s", flat):
            cols = [c.strip() for c in flat[len("SELECT "):flat.index(" FROM ")].split(",")]
            row = bundles.get(tuple(params))
            visible = row is not None and conn.visible(params[0], params[1], shares)
            self.rows = [tuple(memoryview(row[c]) if c == "data" else row[c] for c in cols)] if visible else []
            hook, db.after_bundle_read = db.after_bundle_read, None
            if hook:
                hook()
        elif flat.startswith(f"SELECT data FROM {pd_store.FQ_DESIGN_BUNDLE_CHUNK} WHERE owner = %s"):
            owner, design = params
            self.rows = [(v,) for (o, d, _), v in sorted(chunks.items())
                         if (o, d) == (owner, design) and conn.visible(o, d, shares)]
        else:
            raise AssertionError(f"unexpected SQL: {flat[:80]}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list:
        return [tuple(memoryview(v) for v in row) for row in self.rows]

    def __iter__(self):
        if self.name and self.itersize == 1:
            self.conn.db.named.append(self.name)
        for row in self.rows:
            yield tuple(memoryview(v) for v in row)
