"""A cache restore whose checkpoint links cannot be pointed at the new build dir still counts as a hit and warns."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs POSIX permissions and a non-root user")
def test_an_unwritable_checkpoint_dir_warns_and_keeps_the_hit(tmp_path, monkeypatch):
    rundir_a = tmp_path / "A" / "build" / "par-rundir"
    lef = rundir_a / "pre_route_opt_design" / "libs" / "lef"
    lef.mkdir(parents=True)
    (lef / "sky130_ef_io.lef").symlink_to(rundir_a.parent / "tech-sky130-cache" / "sky130_ef_io.lef")
    (rundir_a / "par-output.json").write_text(json.dumps({"par.outputs.output_gds": f"{rundir_a}/ChipTop.gds"}))
    lef.chmod(0o555)
    try:
        blob = pd_store.tar_directory(rundir_a)
    finally:
        lef.chmod(0o755)

    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_run_with_checkpoint_stream", lambda driver, tag, rundir, fn: fn())
    events, warnings, runs = [], [], []
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: events.append(kind))
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda *a, **k: None)
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: ("par", blob, 100.0, None))
    driver = SimpleNamespace(log=SimpleNamespace(info=lambda msg: None, warning=warnings.append))
    rundir_b = tmp_path / "B" / "build" / "par-rundir"
    lef_b = rundir_b / "pre_route_opt_design" / "libs" / "lef"

    try:
        ok, output = pd_cache.cache_or_run(driver, "par", str(rundir_b), "par-output.json",
                                           run_fn=lambda: runs.append(1) or (True, {}))
    finally:
        if lef_b.exists():
            lef_b.chmod(0o755)

    assert ok and runs == [] and events == ["HIT"]
    assert output == {"par.outputs.output_gds": f"{rundir_b}/ChipTop.gds"}
    assert os.readlink(lef_b / "sky130_ef_io.lef") == str(rundir_a.parent / "tech-sky130-cache" / "sky130_ef_io.lef")
    assert any("pre_route_opt_design/libs/lef/sky130_ef_io.lef" in w for w in warnings)
    assert os.listdir(lef_b) == ["sky130_ef_io.lef"]
