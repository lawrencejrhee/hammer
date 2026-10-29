"""A cache restore into the build dir the blob was stored from, run from another working directory, leaves the
paths into that build dir as they are."""
import json
from types import SimpleNamespace

from hammer.vlsi import pd_cache, pd_store


def test_paths_into_the_unmoved_build_dir_are_not_rewritten_for_the_working_directory(tmp_path, monkeypatch):
    work = tmp_path / "proj" / "vlsi"
    obj = work / "build" / "ChipTop"
    rundir = obj / "syn-rundir"
    rundir.mkdir(parents=True)
    outputs = {
        "synthesis.outputs.output_files": [f"{rundir}/ChipTop.mapped.v"],
        "synthesis.outputs.sdc": f"{rundir}/ChipTop.mapped.sdc",
        "vlsi.inputs.sram_parameters": f"{obj}/sram_generator-output.json",
    }
    (rundir / "ChipTop.mapped.v").write_text("module ChipTop; endmodule\n")
    (rundir / "syn-output.json").write_text(json.dumps(outputs, indent=4))
    (rundir / pd_cache.ORIGIN_MARKER).write_text(json.dumps({"work_dir": str(work)}) + "\n")
    blob = pd_store.tar_directory(rundir)

    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: None)
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda *a, **k: None)
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: ("synthesis", blob, 100.0, None))
    driver = SimpleNamespace(log=SimpleNamespace(info=lambda msg: None, warning=lambda msg: None))
    monkeypatch.chdir(tmp_path / "proj")

    ok, output = pd_cache.cache_or_run(driver, "synthesis", str(rundir), "syn-output.json",
                                       run_fn=lambda: (True, {"ran": "locally"}))

    assert ok and output == outputs
