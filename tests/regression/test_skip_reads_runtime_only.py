from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store


def test_skip_with_local_output_reads_runtime_not_the_blob(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_make_would_rerun", lambda driver, path: False)
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: pytest.fail("downloaded the whole blob"))
    monkeypatch.setattr(pd_store, "stage_blob_runtime", lambda key: (120.0, 300.0))
    events = []
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **kw: events.append((kind, kw)))
    (tmp_path / "syn-output.json").write_text("{}")
    assert pd_cache.try_restore_from_cache(SimpleNamespace(log=None), "synthesis", str(tmp_path), "syn-output.json")
    kind, kw = events[0]
    assert kind == "SKIP_LOCAL" and kw["saved_seconds"] == 120.0 and kw["saved_cpu_seconds"] == 300.0


def test_runtime_query_never_selects_the_data(monkeypatch) -> None:
    seen = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            seen.append(sql)

        def fetchone(self):
            return (12.5, None)

    conn = SimpleNamespace(cursor=lambda: Cur(), close=lambda: None)
    monkeypatch.setattr(pd_store, "_connect", lambda: conn)
    assert pd_store.stage_blob_runtime("k") == (12.5, None)
    assert "data" not in seen[0]
