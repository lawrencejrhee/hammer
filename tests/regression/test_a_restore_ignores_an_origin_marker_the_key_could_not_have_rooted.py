"""A restore ignores an origin marker naming /, the home directory or a directory inside the stored build dir,
which the cache key never names as the working directory, so it leaves the paths of a blob restored into the build
dir it came from as they are."""
import json
from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store


@pytest.mark.parametrize("marker", ["/", "home", "build dir"])
def test_a_marker_the_key_could_not_have_rooted_rewrites_nothing(tmp_path, monkeypatch, marker):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    work = home / "chipyard" / "vlsi"
    obj = work / "build" / "dummy"
    rundir = obj / "syn-rundir"
    rundir.mkdir(parents=True)
    outputs = {
        "synthesis.outputs.output_files": [f"{rundir}/dummy.mapped.v"],
        "synthesis.outputs.output_dir": f"{rundir}/",
        "synthesis.inputs.input_files": [f"{work}/src/top.v"],
        "vlsi.inputs.sram_parameters": f"{obj}/sub/sram_generator-output.json",
    }
    (rundir / "syn-output.json").write_text(json.dumps(outputs, indent=4))
    stored_from = {"/": "/", "home": str(home), "build dir": str(obj / "sub")}[marker]
    (rundir / pd_cache.ORIGIN_MARKER).write_text(json.dumps({"work_dir": stored_from}) + "\n")
    blob = pd_store.tar_directory(rundir)

    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: None)
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda *a, **k: None)
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: ("synthesis", blob, 100.0, None))
    driver = SimpleNamespace(log=SimpleNamespace(info=lambda msg: None, warning=lambda msg: None))
    monkeypatch.chdir(work)

    ok, output = pd_cache.cache_or_run(driver, "synthesis", str(rundir), "syn-output.json",
                                       run_fn=lambda: (True, {"ran": "locally"}))

    assert ok and output == outputs
