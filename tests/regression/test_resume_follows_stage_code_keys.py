import json
import os
import time
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.synthesis.mocksynth import MockSynth
from hammer.vlsi import CLIDriver, code_fingerprints, rtl_check
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
        CLIDriver().main(args=["syn", "-o", str(tmp_path / "output.json"), "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    return cm.value.code


def _marker(rundir: Path) -> dict:
    return json.loads((rundir / MARKER_NAME).read_text())


def _crashed_at_step3(tmp_path: Path, monkeypatch) -> Path:
    """A syn whose step3 failed after the tool confirmed the pre_step2 and pre_step3 checkpoints."""
    real = MockSynth.step3
    failing = {"on": True}

    def step3(self) -> bool:
        return False if failing["on"] else real(self)
    monkeypatch.setattr(MockSynth, "step3", step3)
    _config(tmp_path)
    assert _syn(tmp_path) != 0
    failing["on"] = False
    rundir = tmp_path / "obj" / "syn-rundir"
    assert _marker(rundir)["resumed_from"] is None
    t = time.time() - 100
    for i, step in enumerate(["step2", "step3"]):
        (rundir / f"pre_{step}").write_text("db")
        os.utime(rundir / f"pre_{step}", (t + i, t + i))
    (rundir / "genus.log").write_text("".join(
        f"Finished exporting design database to file 'pre_{s}'\n" for s in ["step2", "step3"]))
    return rundir


def _edit(monkeypatch, name: str, stage: str) -> None:
    real = getattr(code_fingerprints, name)

    def edited(*args, **kwargs):
        value = real(*args, **kwargs)
        hit = args[0] == "hammer.synthesis.mocksynth" if name == "tool_fingerprint" else args[2] == stage
        return "edited-" + value if hit else value
    monkeypatch.setattr(code_fingerprints, name, edited)


def test_unchanged_code_resumes_from_the_newest_checkpoint(tmp_path, monkeypatch) -> None:
    rundir = _crashed_at_step3(tmp_path, monkeypatch)
    key = _marker(rundir)["stage_key"]
    assert _syn(tmp_path) == 0
    assert _marker(rundir)["resumed_from"] == "step3"
    assert _marker(rundir)["stage_key"] == key


@pytest.mark.parametrize("name", ["tool_fingerprint", "hooks_fingerprint"])
def test_a_synthesis_code_change_restarts_syn_from_step1(tmp_path, monkeypatch, name) -> None:
    rundir = _crashed_at_step3(tmp_path, monkeypatch)
    key = _marker(rundir)["stage_key"]
    _edit(monkeypatch, name, "synthesis")
    (tmp_path / "mock" / "step1.txt").unlink()
    assert _syn(tmp_path) == 0
    assert _marker(rundir)["resumed_from"] is None
    assert _marker(rundir)["stage_key"] != key
    assert list(rundir.glob("pre_*")) == []
    assert (tmp_path / "mock" / "step1.txt").exists()


def test_a_drc_hook_change_still_resumes_syn(tmp_path, monkeypatch) -> None:
    rundir = _crashed_at_step3(tmp_path, monkeypatch)
    key = _marker(rundir)["stage_key"]
    _edit(monkeypatch, "hooks_fingerprint", "drc")
    _config(tmp_path, **{"drc.hooks_fingerprint_sha256": "edited", "drc.tool_fingerprint_sha256": "edited"})
    assert _syn(tmp_path) == 0
    assert _marker(rundir)["resumed_from"] == "step3"
    assert _marker(rundir)["stage_key"] == key
