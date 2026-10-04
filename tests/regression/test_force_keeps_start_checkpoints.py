import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, rtl_check


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


def _config(tmp_path: Path, **extra) -> str:
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
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg, cls=HammerJSONEncoder, indent=4))
    return str(path)


def _run(tmp_path: Path, action: str, *flags: str) -> int:
    out = [] if "-o" in flags else ["-o", str(tmp_path / "output.json")]
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=[action, *flags, *out, "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    return cm.value.code


class TestForceWithStartStep:
    def _stale_checkpoint(self, tmp_path: Path, step: str) -> Path:
        rundir = tmp_path / "obj" / "syn-rundir"
        rundir.mkdir(parents=True, exist_ok=True)
        (rundir / f"pre_{step}").write_text("db")
        return rundir / f"pre_{step}"

    def test_force_with_from_step_keeps_the_start_checkpoint(self, tmp_path) -> None:
        _config(tmp_path)
        ck = self._stale_checkpoint(tmp_path, "step3")
        assert _run(tmp_path, "syn", "--force", "--from_step", "step3") == 0
        assert ck.exists()

    def test_force_with_after_step_keeps_the_checkpoints(self, tmp_path) -> None:
        _config(tmp_path)
        ck = self._stale_checkpoint(tmp_path, "step2")
        assert _run(tmp_path, "syn", "--force", "--after_step", "step2") == 0
        assert ck.exists()

    def test_force_alone_still_starts_from_scratch(self, tmp_path) -> None:
        _config(tmp_path)
        ck = self._stale_checkpoint(tmp_path, "step3")
        assert _run(tmp_path, "syn", "--force") == 0
        assert not ck.exists()
