"""A restore run from the home directory, which the cache key never names as the working directory, ignores the
blob's origin marker and places the stored paths the way it does for a blob without one."""
import json
from types import SimpleNamespace

from hammer.vlsi import pd_cache, pd_store


def _outputs(work, obj):
    return {
        "synthesis.outputs.output_files": [f"{obj}/syn-rundir/x.mapped.v"],
        "synthesis.inputs.input_files": [f"{work}/rtl/top.v"],
    }


def test_the_marker_is_not_used_to_point_paths_at_the_home_directory(tmp_path, monkeypatch):
    work_a = tmp_path / "A" / "proj"
    rundir_a = work_a / "build" / "x" / "syn-rundir"
    rundir_a.mkdir(parents=True)
    (rundir_a / "syn-output.json").write_text(json.dumps(_outputs(work_a, work_a / "build" / "x"), indent=4))
    (rundir_a / pd_cache.ORIGIN_MARKER).write_text(json.dumps({"work_dir": str(work_a)}) + "\n")
    blob = pd_store.tar_directory(rundir_a)

    home = tmp_path / "B"
    work_b = home / "proj"
    work_b.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: None)
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda *a, **k: None)
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: ("synthesis", blob, 100.0, None))
    driver = SimpleNamespace(log=SimpleNamespace(info=lambda msg: None, warning=lambda msg: None))
    monkeypatch.chdir(home)

    ok, output = pd_cache.cache_or_run(driver, "synthesis", str(work_b / "build" / "x" / "syn-rundir"),
                                       "syn-output.json", run_fn=lambda: (True, {"ran": "locally"}))

    assert ok and output == _outputs(work_b, work_b / "build" / "x")
