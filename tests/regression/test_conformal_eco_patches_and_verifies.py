import json
import os
import sys
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.logging import HammerVLSILogging
from hammer.vlsi import HammerDriver, HammerDriverOptions, HammerTool

from tests.utils.tool import HammerToolTestHelpers

ECO_STEPS = ["setup_eco_designs", "patch_eco_designs"]

# Stands in for Conformal: answers each report_verification redirect (pre-ECO, then post-ECO) and writes the
# files the dofile names, so the plugin's checks run against what the real tool would leave behind.
FAKE_LEC = """#!{python}
import os, re, sys
open("lec.args", "w").write(" ".join(sys.argv[1:]))
verdicts = iter(["FAIL:NONEQ", os.environ.get("FAKE_LEC_POST_ECO", "PASS")])
for line in open(sys.argv[sys.argv.index("-dofile") + 1]).read().splitlines():
    if re.match(r"redirect \\S+ \\{{report_verification\\}}", line):
        open(line.split()[1], "w").write("6. Compare Results:          " + next(verdicts) + "\\n")
    for pattern in (r"^analyze_eco (\\S+)", r"-file (\\S+)", r"-newfile (\\S+)"):
        m = re.search(pattern, line)
        if m:
            open(m.group(1), "w").write("// written by the fake lec\\n")
"""


@pytest.fixture
def formal(tmp_path, request, monkeypatch):
    """
    A driver for an ECO on a technology with standard cells at a setup and a hold corner and pads at the setup
    corner; run() returns (ok, outputs).
    """
    HammerVLSILogging.clear_callbacks()
    tech_name = request.function.__name__
    tech_dir = HammerToolTestHelpers.create_tech_dir(str(tmp_path), tech_name)

    def libraries(tech: dict) -> dict:
        slow = {"corner": {"nmos": "slow", "pmos": "slow", "temperature": "100 C"}, "supplies": {"VDD": "1.60 V", "GND": "0 V"}}
        fast = {"corner": {"nmos": "fast", "pmos": "fast", "temperature": "-40 C"}, "supplies": {"VDD": "1.95 V", "GND": "0 V"}}
        tech["libraries"] = [
            dict(slow, nldm_liberty_file="slow.lib", provides=[{"lib_type": "stdcell"}]),
            dict(slow, nldm_liberty_file="pads.lib", provides=[{"lib_type": "iocell"}]),
            dict(fast, nldm_liberty_file="fast.lib", provides=[{"lib_type": "stdcell"}]),
        ]
        return tech
    HammerToolTestHelpers.write_tech_json(os.path.join(tech_dir, f"{tech_name}.tech.json"), tech_name, libraries)
    for lib in ("slow.lib", "pads.lib", "fast.lib"):
        Path(tech_dir, lib).write_text('library (cells) {\n  time_unit : "1ns";\n  capacitive_load_unit (1,pf);\n}\n')
    monkeypatch.syspath_prepend(str(tmp_path))

    lec = tmp_path / "lec"
    lec.write_text(FAKE_LEC.format(python=sys.executable))
    lec.chmod(0o755)
    impl = tmp_path / "dummy.sim.v"
    impl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    fixed = tmp_path / "dummy.mapped.v"
    fixed.write_text("module dummy(input a, output b); assign b = ~a; endmodule\n")
    config = tmp_path / "eco.json"
    config.write_text(json.dumps({
        "vlsi.core.technology": tech_name,
        "vlsi.core.formal_tool": "hammer.formal.conformal",
        "vlsi.inputs.mmmc_corners": [
            {"name": "ss_100C_1v60", "type": "setup", "voltage": "1.60 V", "temp": "100 C"},
            {"name": "ff_n40C_1v95", "type": "hold", "voltage": "1.95 V", "temp": "-40 C"},
        ],
        "formal.inputs.check": "eco",
        "formal.inputs.input_files": [str(impl)],
        "formal.inputs.reference_files": [str(fixed)],
        "formal.inputs.top_module": "dummy",
        "formal.conformal.conformal_lec_bin": str(lec),
        "formal.submit.command": "local",
    }, cls=HammerJSONEncoder))
    driver = HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=[str(config)],
                                              log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))
    run_dir = tmp_path / "obj" / "eco-rundir"

    def run(*hooks):
        assert driver.load_formal_tool(str(run_dir))
        return driver.run_formal(list(hooks))

    run.driver = driver
    run.run_dir = run_dir
    run.tech_dir = tech_dir
    run.impl, run.fixed = str(impl), str(fixed)
    return run


def _commands(run_dir: Path) -> list:
    return [line for line in (run_dir / "eco.tcl").read_text().splitlines() if line and not line.startswith("#")]


def _assert_in_order(commands: list, expected: list) -> None:
    """Every expected command appears, in this order (other commands may sit between them)."""
    pos = -1
    for command in expected:
        assert command in commands[pos + 1:], f"{command!r} missing or out of order"
        pos = commands.index(command, pos + 1)


class TestConformalEco:
    def test_eco_steps_replace_the_lec_steps(self, formal) -> None:
        assert formal.driver.load_formal_tool(str(formal.run_dir))
        assert [s.name for s in formal.driver.formal_tool.steps] == ECO_STEPS

    def test_the_dofile_runs_the_flat_eco_flow_in_order(self, formal) -> None:
        ok, _ = formal()
        assert ok
        rundir = str(formal.run_dir)
        expected = [
            "set_log_file eco.log -replace",
            # the setup corner's cells, pads included, so the netlists read; not the hold corner's second copy
            f"read_library {formal.tech_dir}/slow.lib {formal.tech_dir}/pads.lib -liberty -both -replace",
            f"read_design {formal.impl} -golden -replace",
            f"read_design {formal.fixed} -revised -replace",
            "set_root_module dummy -both",
            "set_eco_option -flat",
            "uniquify -all -nolibrary -revised",
            "flatten -matchhierarchy -nolibrary -revised",
            "checkpoint pre_patch_eco_designs -replace",
            "set_system_mode lec",
            "analyze_hier_compare -dofile hier.eco.do -replace -eco_aware -verbose",
            "add_compared_points -all",
            "compare",
            "redirect 01_pre_eco_verification.rpt {report_verification}",
            "redirect 02_pre_eco_nonequivalent.rpt {report_compare_data -class nonequivalent}",
            "compare_eco_hierarchy",
            "redirect 03_eco_check.rpt {report_eco_check -verbose}",
            "redirect 04_eco_hierarchy_noneq.rpt {report_eco_hierarchy -noneq -verbose}",
            f"analyze_eco {rundir}/patch.v -hierarchical -ecopin_dofile ecopin.do -replace",
            "set_system_mode setup",
            "dofile ecopin.do",
            "apply_patch -auto -golden -keephierarchy",
            "set_system_mode lec",
            "add_compared_points -all",
            "compare",
            "redirect 05_post_eco_verification.rpt {report_verification}",
            "redirect 06_post_eco_compare_data.rpt {report_compare_data}",
            f"report_eco_changes -innovus -file {rundir}/eco.invs -replace",
            f"write_eco_design -newfile {rundir}/dummy.eco.v -replace -report 07_ECOprelogics.rpt",
            "checkpoint post_patch_eco_designs -replace",
            "exit",
        ]
        commands = _commands(formal.run_dir)
        _assert_in_order(commands, expected)
        # a checkpoint costs about as much as the ECO, so only the plugin's usual two: after reading and at the end
        assert len([c for c in commands if c.startswith("checkpoint")]) == 2
        assert not any("fast.lib" in c for c in commands)
        args = (formal.run_dir / "lec.args").read_text().split()
        assert args[:5] == ["-ECOGXL", "-nogui", "-color", "-tclmode", "-dofile"]

    def test_a_closed_eco_exports_its_artifacts(self, formal) -> None:
        ok, outputs = formal()
        assert ok
        rundir = str(formal.run_dir)
        assert outputs["formal.outputs.eco_netlist"] == f"{rundir}/dummy.eco.v"
        assert outputs["formal.outputs.eco_patch"] == f"{rundir}/patch.v"
        assert outputs["formal.outputs.eco_innovus_script"] == f"{rundir}/eco.invs"

    def test_an_eco_that_does_not_verify_fails(self, formal, monkeypatch) -> None:
        monkeypatch.setenv("FAKE_LEC_POST_ECO", "FAIL:NONEQ")
        monkeypatch.setattr(HammerVLSILogging, "enable_buffering", True)
        HammerVLSILogging.add_callback(HammerVLSILogging.callback_buffering)
        ok, _ = formal()
        assert not ok
        assert any("post-ECO compare reported FAIL:NONEQ" in line for line in HammerVLSILogging.get_buffer())

    def test_a_stopped_eco_is_not_judged_on_steps_it_did_not_run(self, formal) -> None:
        ok, outputs = formal(HammerTool.make_post_pause_hook("setup_eco_designs"))
        assert ok
        assert "analyze_eco" not in (formal.run_dir / "eco.tcl").read_text()
        assert not any(k.startswith("formal.outputs.eco") for k in outputs)
