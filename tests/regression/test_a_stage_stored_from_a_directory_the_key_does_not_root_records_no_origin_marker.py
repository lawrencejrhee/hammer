"""A stage stored while running from /, the home directory or inside its build dir, none of which the cache key
names as the working directory, records no origin marker, while one run from a working directory does."""
import io
import json
import tarfile
from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_run_with_checkpoint_stream", lambda driver, tag, rundir, fn: fn())
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: None)
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: None)
    blobs = []
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda stage, key, data, **k: blobs.append(data))
    return blobs


def _store_from(tmp_path, monkeypatch, cwd):
    obj = tmp_path / "home" / "proj" / "build"
    rundir = obj / "syn-rundir"
    (obj / "sub").mkdir(parents=True)
    rundir.mkdir()
    (rundir / pd_cache.ORIGIN_MARKER).write_text(json.dumps({"work_dir": "/left/by/an/earlier/restore"}))
    monkeypatch.chdir({"/": "/", "home": tmp_path / "home", "build dir": obj / "sub",
                       "work dir": tmp_path / "home" / "proj"}[cwd])
    driver = SimpleNamespace(obj_dir=str(obj), log=SimpleNamespace(info=lambda msg: None, warning=lambda msg: None))
    ok, _ = pd_cache.cache_or_run(driver, "synthesis", str(rundir), "syn-output.json", run_fn=lambda: (True, {}))
    assert ok
    return rundir


@pytest.mark.parametrize("cwd", ["/", "home", "build dir"])
def test_no_marker_is_stored_from_a_directory_the_key_does_not_root(tmp_path, monkeypatch, cache, cwd):
    rundir = _store_from(tmp_path, monkeypatch, cwd)

    assert not (rundir / pd_cache.ORIGIN_MARKER).exists()
    with tarfile.open(fileobj=io.BytesIO(cache[0]), mode="r:gz") as tar:
        assert not [n for n in tar.getnames() if n.endswith(pd_cache.ORIGIN_MARKER)]


def test_the_working_directory_is_stored_in_the_marker(tmp_path, monkeypatch, cache):
    rundir = _store_from(tmp_path, monkeypatch, "work dir")

    assert json.loads((rundir / pd_cache.ORIGIN_MARKER).read_text()) == {"work_dir": str(tmp_path / "home" / "proj")}
