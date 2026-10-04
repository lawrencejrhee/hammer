from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_store


class _Conn:
    def __init__(self, log):
        self.log = log
        self.autocommit = False

    def cursor(self):
        conn = self

        class Cur:
            rowcount = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                conn.log.append((" ".join(sql.split()), params))
                self.rowcount = 99 if pd_store.FQ_BLOB_CHUNK in sql else 5

        return Cur()

    def close(self):
        pass


@pytest.fixture
def log(monkeypatch):
    log = []
    monkeypatch.setattr(pd_store, "_pg_settings", lambda: {})
    monkeypatch.setattr(pd_store, "psycopg2", SimpleNamespace(connect=lambda **k: _Conn(log)))
    return log


@pytest.mark.parametrize("stage", [None, "par"])
def test_wipe_also_drops_the_chunks(log, stage) -> None:
    assert pd_store.delete_stage_blobs(stage_tag=stage) == 5
    blob_sql, chunk_sql = log[0][0], log[1][0]
    assert blob_sql.startswith(f"DELETE FROM {pd_store.FQ_BLOB}")
    assert (log[0][1] == ("par",)) == (stage == "par")
    assert chunk_sql.startswith(f"DELETE FROM {pd_store.FQ_BLOB_CHUNK}")
    assert "NOT EXISTS" in chunk_sql
