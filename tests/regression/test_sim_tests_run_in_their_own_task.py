import ast
import functools
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from hammer.config import HammerJSONEncoder
from hammer.sim.mocksim import MockSim
from hammer.vlsi import CLIDriver, HammerDriver, HammerDriverOptions, rtl_check
from hammer.vlsi.hammer_build_systems import build_airflow_dag

SIMV = """#!/bin/sh
echo run >> ../../trace
echo "*** PASSED ***" >&2
"""
DAG_FUNCTIONS = {"_task_module", "_module_selected", "_force_requested", "should_run_stage", "_selected_proj_configs",
                 "_with_stage_configs", "run_hammer_action", "sim_tests_"}


# --------------------------------------------------------------------------- the sim-tests action

@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("HAMMER_PD_CACHE", "HAMMER_AIRFLOW_DESIGN"):
        monkeypatch.delenv(name, raising=False)
    compiles = []
    real = MockSim.step1

    @functools.wraps(real)
    def counted(self):
        compiles.append(1)
        return real(self)

    monkeypatch.setattr(MockSim, "step1", counted)
    (tmp_path / "tb.v").write_text("module tb; endmodule\n")
    return compiles


def _write(tmp_path: Path, **extra) -> None:
    config = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.sim_tool": "hammer.sim.mocksim",
        "vlsi.inputs.hierarchical.config_source": "none",
        "sim.inputs.top_module": "tb",
        "sim.inputs.input_files": ["tb.v"],
        "sim.inputs.level": "rtl",
        "sim.inputs.options": [],
        "sim.inputs.execute_sim": False,
        "sim.submit.command": "local",
        "sim.mocksim.temp_folder": str(tmp_path),
        "sim.mocksim.simv_script": SIMV,
        "sim.tests.suites": [{"name": "asm", "tests": ["a", "b"], "pass_regex": "PASSED"}],
    }
    config.update(extra)
    (tmp_path / "config.json").write_text(json.dumps(config, cls=HammerJSONEncoder))


def _hammer(tmp_path: Path, action: str, config: Path, *flags: str) -> int:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=[action, *flags, "-o", str(tmp_path / "output.json"), "-p", str(config),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt"),
                               "--sim_rundir", str(tmp_path / "obj" / "sim-rundir")])
    return cm.value.code


def _compiled(tmp_path: Path) -> Path:
    assert _hammer(tmp_path, "sim", tmp_path / "config.json") == 0
    return tmp_path / "obj" / "sim-rundir" / "sim-output-full.json"


def test_the_tests_action_runs_the_tests_without_compiling(tmp_path, project) -> None:
    compiles = project
    _write(tmp_path)
    full = _compiled(tmp_path)
    assert _hammer(tmp_path, "sim-tests", full, "--sim_tests", "asm") == 0
    assert len(compiles) == 1
    assert len((tmp_path / "trace").read_text().splitlines()) == 2
    results = json.loads((tmp_path / "obj" / "sim-rundir" / "tests" / "results.json").read_text())["results"]
    assert [r["status"] for r in results] == ["PASSED", "PASSED"]


def test_the_tests_action_with_no_compiled_simulator_is_an_error(tmp_path, project, capsys) -> None:
    _write(tmp_path)
    full = _compiled(tmp_path)
    (tmp_path / "obj" / "sim-rundir" / "simv").unlink()
    assert _hammer(tmp_path, "sim-tests", full, "--sim_tests", "asm") != 0
    assert "No compiled simulator" in capsys.readouterr().out


def test_the_tests_action_with_nothing_selected_runs_nothing(tmp_path, project) -> None:
    _write(tmp_path)
    full = _compiled(tmp_path)
    assert _hammer(tmp_path, "sim-tests", full) == 0
    assert not (tmp_path / "trace").exists()


def test_the_tests_action_on_a_design_with_no_suites_is_an_error(tmp_path, project, capsys) -> None:
    _write(tmp_path, **{"sim.tests.suites": []})
    full = _compiled(tmp_path)
    assert _hammer(tmp_path, "sim-tests", full, "--sim_tests", "asm") != 0
    assert "no sim.tests.suites" in capsys.readouterr().out


# --------------------------------------------------------------------------- the generated DAG

@pytest.fixture
def dag(tmp_path, monkeypatch):
    """The generated DAG's task helpers, run against stubs for Airflow, the workspace resolver and subprocess."""
    monkeypatch.setattr(rtl_check, "digest_units", lambda *a, **k: ("0" * 64, []))
    monkeypatch.setattr(rtl_check, "digest_files", lambda *a, **k: ("0" * 64, []))
    (tmp_path / "top.v").write_text("module Top(input a, output b); assign b = a; endmodule\n")
    (tmp_path / "config.json").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.airflow_dags_folder": str(tmp_path / "dags"),
        "vlsi.inputs.hierarchical.config_source": "none",
        "synthesis.inputs.top_module": "Top",
        "synthesis.inputs.input_files": [str(tmp_path / "top.v")],
    }))
    driver = HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=[str(tmp_path / "config.json")],
                                              log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))
    errors = []
    build_airflow_dag(driver, errors.append)
    assert errors == []
    path = Path(driver.obj_dir) / "hammer_dag.py"
    text = path.read_text()
    tree = ast.parse(text)
    kept = [node for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper()
            or isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_STAGE_ONLY_CONFIGS"
            or isinstance(node, ast.FunctionDef) and node.name in DAG_FUNCTIONS]
    commands, state = [], {}

    class Skip(Exception):
        pass

    namespace = {"task": lambda fn=None, **kw: fn if fn else (lambda f: f), "AirflowSkipException": Skip,
                 "AirflowFailException": RuntimeError, "os": __import__("os"),
                 "subprocess": SimpleNamespace(run=lambda cmd, **kw: commands.append(cmd) or SimpleNamespace(returncode=0),
                                               DEVNULL=None)}
    exec(compile(ast.Module(kept, []), str(path), "exec"), namespace)

    sdk = types.ModuleType("airflow.sdk")
    sdk.get_current_context = lambda: state["context"]
    monkeypatch.setitem(sys.modules, "airflow", types.ModuleType("airflow"))
    monkeypatch.setitem(sys.modules, "airflow.sdk", sdk)
    from hammer.shell import hammer_vlsi
    monkeypatch.setattr(hammer_vlsi, "_resolve_workspace_obj_dir", lambda *a, **k: namespace["OBJ_DIR"])
    from hammer.vlsi import pd_notify
    monkeypatch.setattr(pd_notify, "record_stage_outcome", lambda *a, **k: None)

    def context(task_id: str, **conf) -> dict:
        state["context"] = {"task": SimpleNamespace(task_id=task_id, downstream_list=[]),
                            "dag_run": SimpleNamespace(conf={"stage_mails": False, **conf}, dag_id="d", run_id="r"),
                            "ti": SimpleNamespace(task_id=task_id), "params": {}}
        return state["context"]

    return SimpleNamespace(ns=namespace, text=text, commands=commands, context=context, Skip=Skip)


@pytest.mark.parametrize("stage", ["sim_rtl", "sim_syn", "sim_par"])
def test_a_tests_task_runs_the_tests_action_on_its_stage_s_compile(dag, stage) -> None:
    ctx = dag.context(f"module_Top.{stage}_tests", **{stage: True, "sim_tests": ["asm:all", "bmark:fib"]})
    dag.ns["sim_tests_"](stage, "/obj/out.json", "/obj/rundir", **ctx)
    cmd, = dag.commands
    at = cmd.index("sim-tests")
    assert cmd[cmd.index("-p") + 1] == "/obj/out.json"
    assert cmd[cmd.index("--sim_rundir") + 1] == "/obj/rundir"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--sim_tests"] == ["asm:all", "bmark:fib"]
    assert at == len(cmd) - 1


@pytest.mark.parametrize("conf", [{"sim_tests": ["asm"]}, {"sim_rtl": True}, {"sim_rtl": True, "sim_tests": []},
                                  {"sim_rtl": True, "sim_tests": None}, {"power_rtl": True, "sim_tests": ["asm"]},
                                  {"sim_rtl": True, "tests_sim_syn": ["asm"]}, {"sim_rtl": True, "tests_sim_rtl": None},
                                  {"tests_sim_rtl": ["asm"]}])
def test_a_tests_task_skips_unless_its_stage_and_some_tests_are_selected(dag, conf) -> None:
    ctx = dag.context("module_Top.sim_rtl_tests", **conf)
    with pytest.raises(dag.Skip):
        dag.ns["sim_tests_"]("sim_rtl", "/obj/out.json", "/obj/rundir", **ctx)
    assert dag.commands == []


def test_the_compile_task_never_gets_the_test_selection(dag) -> None:
    dag.context("module_Top.sim_rtl", sim_rtl=True, sim_tests=["asm"])
    dag.ns["run_hammer_action"]("sim", ["-p", "x.yml", "--sim_rundir", "/obj/rundir"])
    cmd, = dag.commands
    assert "--sim_tests" not in cmd


def test_each_sim_stage_s_tests_task_follows_its_compile_task(dag) -> None:
    for stage, compile_task in (("sim_rtl", "s_rtl"), ("sim_syn", "s_syn"), ("sim_par", "s_par")):
        assert f'{compile_task} >> sim_tests_.override(task_id="{stage}_tests")(\n' \
               f'                "{stage}", paths_dict[\'{stage}_out\'], paths_dict[\'{stage}_run_dir\'])' in dag.text


def _selections(cmd) -> list:
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "--sim_tests"]


@pytest.mark.parametrize("stage", ["sim_rtl", "sim_syn", "sim_par"])
def test_each_tests_task_runs_only_its_own_stage_s_selection(dag, stage) -> None:
    conf = {stage: True, "tests_sim_rtl": ["asm:all", "bmark:all"], "tests_sim_syn": ["bmark:fib"],
            "tests_sim_par": ["asm:addi"]}
    dag.ns["sim_tests_"](stage, "/obj/out.json", "/obj/rundir", **dag.context(f"module_Top.{stage}_tests", **conf))
    cmd, = dag.commands
    assert _selections(cmd) == conf[f"tests_{stage}"]


def test_a_stage_s_own_selection_wins_over_the_shared_one(dag) -> None:
    ctx = dag.context("module_Top.sim_syn_tests", sim_syn=True, sim_tests=["asm:all"], tests_sim_syn=["bmark:fib"])
    dag.ns["sim_tests_"]("sim_syn", "/obj/out.json", "/obj/rundir", **ctx)
    cmd, = dag.commands
    assert _selections(cmd) == ["bmark:fib"]


def test_a_stage_with_no_selection_of_its_own_uses_the_shared_one(dag) -> None:
    ctx = dag.context("module_Top.sim_par_tests", sim_par=True, sim_tests=["asm:all"], tests_sim_rtl=["bmark:fib"])
    dag.ns["sim_tests_"]("sim_par", "/obj/out.json", "/obj/rundir", **ctx)
    cmd, = dag.commands
    assert _selections(cmd) == ["asm:all"]


def test_filling_one_stage_s_tests_does_not_pull_in_that_stage(dag) -> None:
    tests_task = SimpleNamespace(task_id="module_Top.sim_syn_tests", downstream_list=[])
    ctx = dag.context("module_Top.sim_syn", sim_rtl=True, tests_sim_syn=["bmark:fib"])
    ctx["task"] = SimpleNamespace(task_id="module_Top.sim_syn", downstream_list=[tests_task])
    assert dag.ns["should_run_stage"]("sim_syn", ctx) is False
    # what the conf key names avoid: a key equal to the tests task's id would pull the compile in
    ctx["dag_run"].conf["sim_syn_tests"] = ["bmark:fib"]
    assert dag.ns["should_run_stage"]("sim_syn", ctx) is True


def test_the_trigger_form_has_an_optional_field_per_sim_stage_in_one_panel(dag) -> None:
    tree = ast.parse(dag.text)
    params = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "tests_sim_rtl"
                                                        for k in node.keys))
    names = [k.value for k in params.keys]
    assert "sim_tests" not in names
    for stage in ("sim_rtl", "sim_syn", "sim_par"):
        call = params.values[names.index(f"tests_{stage}")]
        kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
        assert kwargs["default"] == []
        assert "null" in kwargs["type"]
        assert kwargs["section"] == "Sim tests (for sim_rtl, sim_syn, sim_par)"
        assert stage in kwargs["title"]
