"""A cache restore whose generated scripts cannot be pointed at the new rundir still counts as a hit and warns."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs POSIX permissions and a non-root user")
def test_an_unwritable_generated_scripts_dir_warns_and_keeps_the_hit(tmp_path, monkeypatch):
    rundir_a = tmp_path / "A" / "build" / "par-rundir"
    scripts = rundir_a / "generated-scripts"
    scripts.mkdir(parents=True)
    (scripts / "open_chip").write_text(f"cd {rundir_a}\n")
    (rundir_a / "par-output.json").write_text(json.dumps({"par.outputs.output_gds": f"{rundir_a}/ChipTop.gds"}))
    scripts.chmod(0o555)
    try:
        blob = pd_store.tar_directory(rundir_a)
    finally:
        scripts.chmod(0o755)

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

    try:
        ok, output = pd_cache.cache_or_run(driver, "par", str(rundir_b), "par-output.json",
                                           run_fn=lambda: runs.append(1) or (True, {}))
    finally:
        if (rundir_b / "generated-scripts").exists():
            (rundir_b / "generated-scripts").chmod(0o755)

    assert ok and runs == [] and events == ["HIT"]
    assert output == {"par.outputs.output_gds": f"{rundir_b}/ChipTop.gds"}
    assert any("open_chip" in w for w in warnings)
