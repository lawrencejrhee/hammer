import pytest

from hammer.config.config_src import HammerDatabase
from hammer.vlsi import pd_cache, pd_store


@pytest.mark.parametrize("value", ["", "0", "off", "OFF", "FALSE", "False", "No", " false "])
def test_off_spellings_keep_the_cache_off(monkeypatch, value) -> None:
    monkeypatch.setenv("HAMMER_PD_CACHE", value)
    assert not pd_cache.is_cache_enabled()


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON"])
def test_on_spellings_turn_the_cache_on(monkeypatch, value) -> None:
    monkeypatch.setenv("HAMMER_PD_CACHE", value)
    assert pd_cache.is_cache_enabled()


def test_master_mirror_respects_off(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HAMMER_PD_CACHE", "OFF")
    monkeypatch.setenv("HAMMER_AIRFLOW_DESIGN", "d")
    monkeypatch.setattr(pd_store, "store_artifact", lambda *a, **k: pytest.fail("mirrored with the cache off"))
    monkeypatch.setattr(pd_store, "store_master_database", lambda *a, **k: pytest.fail("mirrored with the cache off"))
    HammerDatabase()._mirror_master_to_db(tmp_path / "master_database.json", "{}")
