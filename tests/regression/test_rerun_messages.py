import json
from pathlib import Path

import pytest

from hammer.vlsi import CLIDriver


@pytest.fixture(autouse=True)
def _no_shared_state(monkeypatch):
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


def _syn(tmp_path: Path, step2_succeeds: bool) -> int:
    rtl = tmp_path / "top.v"
    rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (tmp_path / "mock").mkdir(exist_ok=True)
    (tmp_path / "config.json").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.inputs.hierarchical.config_source": "none",
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(rtl)],
        "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
        "synthesis.mocksynth.step2_succeeds": step2_succeeds,
    }))
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["syn", "-o", str(tmp_path / "output.json"), "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    return cm.value.code


def test_successful_syn_does_not_report_a_failure(tmp_path, capsys) -> None:
    assert _syn(tmp_path, True) == 0
    out = capsys.readouterr().out
    assert "STAGE FAILED" not in out
    assert "marking syn needs-rerun until this run completes" in out
    master = json.loads((tmp_path / "obj" / "master_database.json").read_text())
    assert master["syn.needsToRerun"] is False


def test_failed_syn_still_reports_the_failure(tmp_path, capsys) -> None:
    assert _syn(tmp_path, False) != 0
    assert "STAGE FAILED, REQUIRING RERUN" in capsys.readouterr().out
    master = json.loads((tmp_path / "obj" / "master_database.json").read_text())
    assert master["syn.needsToRerun"] is True
