import json
import os
import time
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, rtl_check, substep_resume
from hammer.vlsi.substep_resume import MARKER_NAME, plan_resume, record_attempt


def _attempt(rundir, log, steps, t):
    for i, step in enumerate(steps):
        (rundir / f"pre_{step}").write_text(f"db {step} {t}")
        os.utime(rundir / f"pre_{step}", (t + i, t + i))
    (rundir / log).write_text("".join(
        f"Finished exporting design database to file 'pre_{s}'\n" for s in steps))
    os.utime(rundir / log, (t + len(steps), t + len(steps)))


def _present(rundir):
    return sorted(p.name[len("pre_"):] for p in rundir.glob("pre_*"))


@pytest.fixture
def key(monkeypatch):
    current = {"key": "K1"}
    monkeypatch.setattr(substep_resume, "_stage_key", lambda driver, tag: current["key"])
    monkeypatch.delenv("HAMMER_SUBSTEP_RESUME", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    return current


def _plan(rundir):
    plan = plan_resume(None, "synthesis", str(rundir), "syn-output.json", "genus.log")
    return plan["step"] if plan else None


def _start(rundir, resumed_from):
    record_attempt(None, "synthesis", str(rundir), resumed_from)
    return os.path.getmtime(rundir / MARKER_NAME)


class TestCompletedRunAfterKeyChange:
    def _completed(self, tmp_path):
        _start(tmp_path, None)
        _attempt(tmp_path, "genus.log", ["a", "b", "c"], 1000)
        (tmp_path / "syn-output.json").write_text("{}")
        os.utime(tmp_path / "syn-output.json", (2000, 2000))

    def test_new_inputs_drop_the_completed_runs_checkpoints(self, tmp_path, key) -> None:
        self._completed(tmp_path)
        key["key"] = "K2"
        assert _plan(tmp_path) is None
        _start(tmp_path, None)
        assert _present(tmp_path) == []

    def test_burned_ladder_never_reaches_the_old_inputs(self, tmp_path, key) -> None:
        self._completed(tmp_path)
        key["key"] = "K2"
        assert _plan(tmp_path) is None
        _start(tmp_path, None)
        _attempt(tmp_path, "genus.log1", ["a"], 3000)
        assert _plan(tmp_path) == "a"
        started = _start(tmp_path, "a")
        (tmp_path / "genus.log2").write_text("ERROR: failed to load checkpoint\n")
        os.utime(tmp_path / "genus.log2", (started + 10, started + 10))
        assert _plan(tmp_path) is None

    def test_same_inputs_keep_the_completed_runs_checkpoints(self, tmp_path, key) -> None:
        self._completed(tmp_path)
        assert _plan(tmp_path) is None
        _start(tmp_path, None)
        assert _present(tmp_path) == ["a", "b", "c"]

    def test_disabled_resume_still_never_adopts_old_checkpoints(self, tmp_path, key, monkeypatch) -> None:
        self._completed(tmp_path)
        monkeypatch.setenv("HAMMER_SUBSTEP_RESUME", "0")
        key["key"] = "K2"
        assert _plan(tmp_path) is None
        _start(tmp_path, None)
        assert _present(tmp_path) == []
        assert json.loads((tmp_path / MARKER_NAME).read_text())["stage_key"] == "K2"


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture
def cli(monkeypatch):
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


def _run(tmp_path: Path, action: str, *flags: str) -> int:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=[action, *flags, "-o", str(tmp_path / "output.json"),
                               "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    return cm.value.code


def test_syn_after_a_config_change_drops_the_completed_runs_checkpoints(tmp_path, cli) -> None:
    _config(tmp_path)
    assert _run(tmp_path, "syn") == 0
    rundir = tmp_path / "obj" / "syn-rundir"
    old_key = json.loads((rundir / MARKER_NAME).read_text())["stage_key"]
    t = time.time() - 1000
    _attempt(rundir, "genus.log", ["step2", "step3", "step4"], t)
    os.utime(rundir / "syn-output.json", (t + 100, t + 100))
    _config(tmp_path, **{"synthesis.mocksynth.step3": "changed"})
    assert _run(tmp_path, "syn") == 0
    assert _present(rundir) == []
    assert json.loads((rundir / MARKER_NAME).read_text())["stage_key"] != old_key
