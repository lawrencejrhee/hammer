"""A cache restore into a pulled copy that sits inside the working directory the blob was stored from points
each stored path at the pulled copy once."""
import json
from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store


def _outputs(work, obj):
    return {
        "synthesis.outputs.output_files": [f"{obj}/syn-rundir/ChipTop.mapped.v"],
        "synthesis.outputs.sdc": f"{obj}/syn-rundir/ChipTop.mapped.sdc",
        "synthesis.inputs.input_files": [f"{work}/rtl/ChipTop.sv"],
        "vlsi.inputs.sram_parameters": f"{obj}/sram_generator-output.json",
    }


def _blob(work, obj):
    rundir = obj / "syn-rundir"
    rundir.mkdir(parents=True)
    (rundir / "ChipTop.mapped.v").write_text("module ChipTop; endmodule\n")
    (rundir / "syn-output.json").write_text(json.dumps(_outputs(work, obj), indent=4))
    (rundir / pd_cache.ORIGIN_MARKER).write_text(json.dumps({"work_dir": str(work)}) + "\n")
    return pd_store.tar_directory(rundir)


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_run_with_checkpoint_stream", lambda driver, tag, rundir, fn: fn())
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: None)
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda *a, **k: None)
    state = SimpleNamespace(blob=None, runs=[])
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: ("synthesis", state.blob, 100.0, None))
    state.driver = SimpleNamespace(log=SimpleNamespace(info=lambda msg: None, warning=lambda msg: None))
    return state


@pytest.mark.parametrize("restore", ["hit", "skip_path"])
def test_a_pull_dir_inside_the_stored_work_dir_gets_each_path_rewritten_once(tmp_path, cache, monkeypatch, restore):
    work = tmp_path / "alice" / "project"
    cache.blob = _blob(work, work / "build" / "ChipTop")
    pulled = work / "build-from-alice"
    obj = pulled / "build" / "ChipTop"
    obj.mkdir(parents=True)
    monkeypatch.chdir(pulled)
    rundir = obj / "syn-rundir"

    if restore == "hit":
        ok, output = pd_cache.cache_or_run(cache.driver, "synthesis", str(rundir), "syn-output.json",
                                           run_fn=lambda: cache.runs.append(1) or (True, {}))
        assert ok and cache.runs == []
    else:
        assert pd_cache.try_restore_from_cache(cache.driver, "synthesis", str(rundir), "syn-output.json")

    assert json.loads((rundir / "syn-output.json").read_text()) == _outputs(pulled, obj)
