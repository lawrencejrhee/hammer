import json
import os
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, cli_driver

EXTRACTION = """\t\t\t*****************************************************
\t\t\t**                L V S  R E P O R T               **
\t\t\t*****************************************************
Report File Name         : {run}/dummy.lvs_results
LVS Comparison Report    : {run}/dummy.lvs_results.cls
"""

COMPARISON = """##############################################################################################################
#####                                       Pegasus LVS COMPARISON
#####  Version                       :   23.10-p015
#####  Top Cell                      :   dummy  <vs>  dummy
#####                                :      @     @
#####                                :       @   @
#####                                :        @ @
#####  Run Result                    :     {result}
#####                                :        @ @
#####                                :       @   @
#####                                :      @     @
#####  Run Summary                   :   [ERROR] Connectivity Mismatches
##############################################################################################################
"""


def _reports(run: Path, result=None, comparison_age: int = 20) -> None:
    run.mkdir(parents=True, exist_ok=True)
    extraction = run / "dummy.lvs_results"
    extraction.write_text(EXTRACTION.format(run=run))
    if result is None:
        return
    comparison = run / "dummy.lvs_results.cls"
    comparison.write_text(COMPARISON.format(result=result))
    st = extraction.stat()
    os.utime(comparison, (st.st_atime, st.st_mtime + comparison_age))


@pytest.mark.parametrize("result, verdict", [
    ("MATCH", "CORRECT"),
    ("MATCH WITH WARNINGS", "CORRECT"),
    ("MISMATCH", "INCORRECT"),
    ("SOMETHING ELSE", "NOT COMPARED"),
])
def test_run_result_maps_to_a_verdict(tmp_path, result, verdict):
    _reports(tmp_path, result)
    assert cli_driver._pegasus_lvs_verdict(str(tmp_path), "dummy") == verdict


def test_missing_comparison_report_means_not_compared(tmp_path):
    _reports(tmp_path)
    assert cli_driver._pegasus_lvs_verdict(str(tmp_path), "dummy") == "NOT COMPARED"


def test_comparison_report_from_an_earlier_run_is_not_trusted(tmp_path):
    _reports(tmp_path, "MATCH", comparison_age=-60)
    assert cli_driver._pegasus_lvs_verdict(str(tmp_path), "dummy") == "NOT COMPARED"


def test_no_pegasus_reports_leaves_the_verdict_to_the_tool(tmp_path):
    assert cli_driver._pegasus_lvs_verdict(str(tmp_path), "dummy") is None


def test_comparison_report_without_a_run_result_means_not_compared(tmp_path):
    _reports(tmp_path, "MATCH")
    (tmp_path / "dummy.lvs_results.cls").write_text("truncated\n")
    st = (tmp_path / "dummy.lvs_results").stat()
    os.utime(tmp_path / "dummy.lvs_results.cls", (st.st_atime, st.st_mtime + 20))
    assert cli_driver._pegasus_lvs_verdict(str(tmp_path), "dummy") == "NOT COMPARED"


def _lvs(tmp_path: Path, capsys, monkeypatch) -> (int, str):
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    gds = tmp_path / "dummy.gds"
    gds.write_text("gds\n")
    netlist = tmp_path / "dummy.v"
    netlist.write_text("module dummy(); endmodule\n")
    (tmp_path / "config.json").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.lvs_tool": "hammer.lvs.mocklvs",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "lvs.inputs.top_module": "dummy",
        "lvs.inputs.layout_file": str(gds),
        "lvs.inputs.schematic_files": [str(netlist)],
    }, cls=HammerJSONEncoder))
    capsys.readouterr()
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["lvs", "-p", str(tmp_path / "config.json"), "-o", str(tmp_path / "out.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    return cm.value.code, capsys.readouterr().out


def _master_flag(tmp_path: Path):
    master = tmp_path / "obj" / "master_database.json"
    return json.loads(master.read_text()).get("lvs.needsToRerun") if master.exists() else None


def test_lvs_action_fails_on_a_pegasus_mismatch(tmp_path, capsys, monkeypatch):
    _reports(tmp_path / "obj" / "lvs-rundir", "MISMATCH")
    code, out = _lvs(tmp_path, capsys, monkeypatch)
    assert code != 0
    assert "INCORRECT" in out and "dummy.lvs_results.cls" in out
    assert _master_flag(tmp_path) is not False


def test_lvs_action_passes_on_a_pegasus_match(tmp_path, capsys, monkeypatch):
    _reports(tmp_path / "obj" / "lvs-rundir", "MATCH")
    code, out = _lvs(tmp_path, capsys, monkeypatch)
    assert code == 0, out
    assert "LVS report verdict: CORRECT" in out


def test_lvs_action_without_pegasus_reports_keeps_the_tool_status(tmp_path, capsys, monkeypatch):
    code, out = _lvs(tmp_path, capsys, monkeypatch)
    assert code == 0, out
    assert "LVS report verdict" not in out
