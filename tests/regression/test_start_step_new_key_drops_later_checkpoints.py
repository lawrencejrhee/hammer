import json
import os
import time
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, rtl_check, substep_resume
from hammer.vlsi.substep_resume import MARKER_NAME, record_attempt


def _attempt(rundir, steps, t):
    for i, step in enumerate(steps):
        (rundir / f"pre_{step}").write_text(f"db {step} {t}")
        os.utime(rundir / f"pre_{step}", (t + i, t + i))
    (rundir / "genus.log").write_text("".join(
        f"Finished exporting design database to file 'pre_{s}'\n" for s in steps))
    os.utime(rundir / "genus.log", (t + len(steps), t + len(steps)))


def _present(rundir):
    return sorted(p.name[len("pre_"):] for p in rundir.glob("pre_*"))


@pytest.fixture
def rundir(tmp_path, monkeypatch):
    monkeypatch.setattr(substep_resume, "_stage_key", lambda driver, tag: "K2")
    (tmp_path / MARKER_NAME).write_text(json.dumps({"stage_key": "K1"}))
    _attempt(tmp_path, ["a", "b", "c", "d", "e"], 1000)
    return tmp_path


def _record(rundir, resumed_from=None, **kw):
    record_attempt(None, "synthesis", str(rundir), resumed_from, log_name="genus.log", **kw)


class TestRecordAttemptUnderANewKey:
    def test_from_step_keeps_the_start_and_earlier_steps(self, rundir) -> None:
        _record(rundir, start_step="c")
        assert _present(rundir) == ["a", "b", "c"]

    def test_after_step_keeps_the_step_it_starts_from(self, rundir) -> None:
        _record(rundir, start_step="b", start_inclusive=False)
        assert _present(rundir) == ["a", "b", "c"]

    def test_step_order_comes_from_a_single_attempt(self, rundir) -> None:
        (rundir / "genus.log1").write_text("".join(
            f"Finished exporting design database to file 'pre_{s}'\n" for s in ["d", "e"]))
        os.utime(rundir / "genus.log1", (500, 500))
        _record(rundir, start_step="c")
        assert _present(rundir) == ["a", "b", "c"]

    def test_after_the_unannounced_first_step_keeps_what_the_run_reads(self, rundir) -> None:
        _record(rundir, start_step="init", start_inclusive=False,
                step_names=["init", "a", "b", "c", "d", "e"])
        assert _present(rundir) == ["a"]

    def test_after_step_takes_the_next_step_from_the_logs_over_the_static_list(self, rundir) -> None:
        _record(rundir, start_step="b", start_inclusive=False,
                step_names=["a", "b", "d", "e"])
        assert _present(rundir) == ["a", "b", "c"]

    def test_after_step_keeps_a_hook_inserted_step_it_reads(self, rundir) -> None:
        _record(rundir, start_step="init", start_inclusive=False,
                step_names=["init", "b", "c", "d", "e"])
        assert _present(rundir) == ["a", "b"]

    def test_unannounced_from_step_keeps_only_itself(self, rundir) -> None:
        (rundir / "pre_x").write_text("fetched by id")
        _record(rundir, start_step="x")
        assert _present(rundir) == ["x"]

    def test_automatic_resume_keeps_only_its_checkpoint(self, rundir) -> None:
        _record(rundir, resumed_from="c")
        assert _present(rundir) == ["c"]

    def test_run_from_the_first_step_keeps_nothing(self, rundir) -> None:
        _record(rundir)
        assert _present(rundir) == []
        assert json.loads((rundir / MARKER_NAME).read_text())["stage_key"] == "K2"

    def test_unchanged_key_keeps_everything(self, rundir, monkeypatch) -> None:
        monkeypatch.setattr(substep_resume, "_stage_key", lambda driver, tag: "K1")
        _record(rundir, start_step="c")
        assert _present(rundir) == ["a", "b", "c", "d", "e"]


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


class TestSynStartStep:
    def _synced(self, tmp_path):
        _config(tmp_path)
        assert _run(tmp_path, "syn") == 0
        rundir = tmp_path / "obj" / "syn-rundir"
        _attempt(rundir, ["step2", "step3", "step4"], time.time() - 1000)
        return rundir

    def test_from_step_after_a_config_change_drops_later_checkpoints(self, tmp_path, cli) -> None:
        rundir = self._synced(tmp_path)
        _config(tmp_path, **{"synthesis.mocksynth.step4": "changed"})
        assert _run(tmp_path, "syn", "--from_step", "step3") == 0
        assert _present(rundir) == ["step2", "step3"]

    def test_after_step_after_a_config_change_drops_later_checkpoints(self, tmp_path, cli) -> None:
        rundir = self._synced(tmp_path)
        _config(tmp_path, **{"synthesis.mocksynth.step4": "changed"})
        assert _run(tmp_path, "syn", "--after_step", "step2") == 0
        assert _present(rundir) == ["step2", "step3"]

    def test_after_the_first_step_after_a_config_change_drops_later_checkpoints(self, tmp_path, cli) -> None:
        rundir = self._synced(tmp_path)
        _config(tmp_path, **{"synthesis.mocksynth.step4": "changed"})
        assert _run(tmp_path, "syn", "--after_step", "step1") == 0
        assert _present(rundir) == ["step2"]

    def test_from_step_with_unchanged_inputs_keeps_every_checkpoint(self, tmp_path, cli) -> None:
        rundir = self._synced(tmp_path)
        assert _run(tmp_path, "syn", "--from_step", "step3") == 0
        assert _present(rundir) == ["step2", "step3", "step4"]
