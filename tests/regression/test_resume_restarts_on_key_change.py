import json
import os
import time
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, rtl_check
from hammer.vlsi.substep_resume import MARKER_NAME


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN", raising=False)
    monkeypatch.delenv("HAMMER_SUBSTEP_RESUME", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


def _config(tmp_path: Path, **extra) -> None:
    rtl = tmp_path / "top.v"
    rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (tmp_path / "mock").mkdir(exist_ok=True)
    cfg = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(rtl)],
        "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
    }
    cfg.update(extra)
    (tmp_path / "config.json").write_text(json.dumps(cfg, cls=HammerJSONEncoder, indent=4))


def _syn(tmp_path: Path) -> int:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["syn", "-o", str(tmp_path / "output.json"),
                               "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    return cm.value.code


def _crashed_attempt(tmp_path: Path) -> Path:
    """A syn that completed once, then a later attempt under the same key that
    confirmed pre_step2 and pre_step3 before it died."""
    _config(tmp_path)
    assert _syn(tmp_path) == 0
    rundir = tmp_path / "obj" / "syn-rundir"
    t = time.time() - 1000
    os.utime(rundir / "syn-output.json", (t, t))
    for i, step in enumerate(["step2", "step3"]):
        (rundir / f"pre_{step}").write_text("db")
        os.utime(rundir / f"pre_{step}", (t + 10 + i, t + 10 + i))
    (rundir / "genus.log").write_text("".join(
        f"Finished exporting design database to file 'pre_{s}'\n" for s in ["step2", "step3"]))
    os.utime(rundir / "genus.log", (t + 20, t + 20))
    master = tmp_path / "obj" / "master_database.json"
    db = json.loads(master.read_text())
    db["syn.needsToRerun"] = True
    master.write_text(json.dumps(db))
    return rundir


def _marker(rundir: Path) -> dict:
    return json.loads((rundir / MARKER_NAME).read_text())


def test_unchanged_key_resumes_from_the_newest_checkpoint(tmp_path) -> None:
    rundir = _crashed_attempt(tmp_path)
    key = _marker(rundir)["stage_key"]
    assert _syn(tmp_path) == 0
    assert _marker(rundir)["resumed_from"] == "step3"
    assert _marker(rundir)["stage_key"] == key
    assert sorted(p.name for p in rundir.glob("pre_*")) == ["pre_step2", "pre_step3"]


def test_changed_stage_key_cleans_and_restarts(tmp_path) -> None:
    rundir = _crashed_attempt(tmp_path)
    key = _marker(rundir)["stage_key"]
    _config(tmp_path, **{"synthesis.mocksynth.step1": "edited"})
    assert _syn(tmp_path) == 0
    assert _marker(rundir)["resumed_from"] is None
    assert _marker(rundir)["stage_key"] != key
    assert list(rundir.glob("pre_*")) == []
