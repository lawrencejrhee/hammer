"""A par rundir stored from one clone and restored into another clone points its open_chip script, enter file
and post_* checkpoint links at the new rundir, and leaves logs, tool scripts and checkpoints as stored."""
import json
import os
from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store

OPEN_CHIP = """#!/bin/bash
        cd {run_dir}
        source enter
        $INNOVUS_BIN -common_ui -win -files {run_dir}/generated-scripts/open_chip.tcl
                """


def _par_rundir(work, rundir):
    """A par-rundir shaped like the one Innovus leaves: absolute paths in its scripts, logs and checkpoints."""
    (rundir / "generated-scripts").mkdir(parents=True)
    (rundir / "generated-scripts" / "open_chip").write_text(OPEN_CHIP.format(run_dir=rundir))
    (rundir / "generated-scripts" / "open_chip").chmod(0o755)
    (rundir / "generated-scripts" / "open_chip.tcl").write_text("set_db design_process_node 7\nread_db latest")
    (rundir / "enter").write_text(f"export INNOVUS_BIN=/tools/cadence/bin/innovus\n"
                                  f"export PATH='{work}/.venv/bin:/usr/bin'")
    stored = {
        "par.tcl": f"source {rundir}/mmmc.tcl\nwrite_db {rundir}/pre_route\n",
        "mmmc.tcl": f"create_constraint_mode -sdc_files {{{work}/build/ChipTop/syn-rundir/ChipTop.mapped.sdc}}\n",
        "innovus.log": f"Sourcing {rundir}/par.tcl\n",
        "pre_place/ChipTop.db": f"cwd {rundir}\n",
        "pre_route/ChipTop.db": f"cwd {rundir}\n",
        "post_route/ChipTop.db": f"cwd {rundir}\n",
    }
    for rel, body in stored.items():
        (rundir / rel).parent.mkdir(exist_ok=True)
        (rundir / rel).write_text(body)
    (rundir / "post_init").symlink_to(rundir / "pre_place")
    (rundir / "post_place").symlink_to(rundir / "pre_route")
    (rundir / "latest").symlink_to("post_route")
    (rundir / "rtl").symlink_to(work / "rtl")
    (rundir / "par-output.json").write_text(json.dumps({
        "par.outputs.output_gds": f"{rundir}/ChipTop.gds",
        "par.outputs.output_netlist": f"{rundir}/ChipTop.lvs.v",
    }, indent=4))
    (rundir / pd_cache.ORIGIN_MARKER).write_text(json.dumps({"work_dir": str(work)}) + "\n")
    return stored


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_run_with_checkpoint_stream", lambda driver, tag, rundir, fn: fn())
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: None)
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda *a, **k: None)
    state = SimpleNamespace(blob=None, runs=[], warnings=[])
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: ("par", state.blob, 100.0, None))
    state.driver = SimpleNamespace(log=SimpleNamespace(info=lambda msg: None, warning=state.warnings.append))
    return state


@pytest.mark.parametrize("restore", ["hit", "skip_path"])
def test_open_chip_enter_and_post_links_follow_the_restore_into_another_clone(tmp_path, cache, monkeypatch,
                                                                             restore):
    work_a, work_b = tmp_path / "s12345" / "repo", tmp_path / "s12346" / "repo"
    rundir_a = work_a / "build" / "ChipTop" / "par-rundir"
    stored = _par_rundir(work_a, rundir_a)
    cache.blob = pd_store.tar_directory(rundir_a)
    rundir_b = work_b / "build" / "ChipTop" / "par-rundir"
    work_b.mkdir(parents=True)
    monkeypatch.chdir(work_b)

    if restore == "hit":
        ok, _ = pd_cache.cache_or_run(cache.driver, "par", str(rundir_b), "par-output.json",
                                      run_fn=lambda: cache.runs.append(1) or (True, {}))
        assert ok and cache.runs == []
    else:
        assert pd_cache.try_restore_from_cache(cache.driver, "par", str(rundir_b), "par-output.json")

    open_chip = rundir_b / "generated-scripts" / "open_chip"
    assert open_chip.read_text() == OPEN_CHIP.format(run_dir=rundir_b)
    assert os.stat(open_chip).st_mode & 0o777 == 0o755
    assert (rundir_b / "enter").read_text() == ("export INNOVUS_BIN=/tools/cadence/bin/innovus\n"
                                                f"export PATH='{work_b}/.venv/bin:/usr/bin'")
    assert os.readlink(rundir_b / "post_init") == "pre_place"
    assert os.readlink(rundir_b / "post_place") == "pre_route"
    assert (rundir_b / "post_place").resolve() == (rundir_b / "pre_route").resolve()
    assert os.readlink(rundir_b / "latest") == "post_route"
    assert os.readlink(rundir_b / "rtl") == str(work_b / "rtl")
    assert {rel: (rundir_b / rel).read_text() for rel in stored} == stored
    assert str(rundir_b) in (rundir_b / "par-output.json").read_text()
    assert cache.warnings == []
