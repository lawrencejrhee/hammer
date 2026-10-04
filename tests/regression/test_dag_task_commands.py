import json
import os
import sys
import types
from pathlib import Path

import pytest

from hammer.vlsi import HammerDriver, HammerDriverOptions
from hammer.vlsi.hammer_build_systems import build_airflow_dag


class _FailException(Exception):
    pass


class _SkipException(Exception):
    pass


def _task(fn=None, **kw):
    return fn if fn is not None else (lambda f: f)


def _airflow_stubs(monkeypatch, context: dict) -> None:
    def module(name: str, **attrs) -> None:
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)

    module("pendulum", datetime=lambda *a, **k: None)
    module("airflow")
    module("airflow.decorators", task=_task, dag=lambda *a, **k: (lambda f: (lambda: None)))
    module("airflow.models", Param=lambda *a, **k: None)
    module("airflow.utils")
    module("airflow.utils.task_group", TaskGroup=object)
    module("airflow.utils.trigger_rule", TriggerRule=types.SimpleNamespace(ALL_DONE="all_done", NONE_FAILED="none_failed"))
    module("airflow.exceptions", AirflowFailException=_FailException, AirflowSkipException=_SkipException)
    module("airflow.sdk", get_current_context=lambda: context)


def _write(path: Path, cfg: dict) -> str:
    path.write_text(json.dumps(cfg, indent=4))
    return str(path)


@pytest.fixture
def dag(tmp_path, monkeypatch):
    """A generated flat DAG, exec'd with Airflow stubbed; run(...) calls one of its functions with a trigger conf."""
    monkeypatch.setenv("HAMMER_DAGS_FOLDER", str(tmp_path / "dags"))
    monkeypatch.setenv("AIRFLOW_HOME", str(tmp_path / "airflow"))
    rtl = tmp_path / "top.v"
    rtl.write_text("module Top(input a, output b); assign b = a; endmodule\n")
    base = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "synthesis.inputs.top_module": "Top",
        "synthesis.inputs.input_files": [str(rtl)],
    }
    a = _write(tmp_path / "a.yml", base)
    b = _write(tmp_path / "b.yml", base)
    driver = HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=[a],
                                              log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))
    build_airflow_dag(driver, lambda e: None, proj_confs_by_tools={"a": [a], "b": [b]}, default_tools="a")
    text = (tmp_path / "obj" / "hammer_dag.py").read_text()

    import hammer.shell.hammer_vlsi as hammer_vlsi
    monkeypatch.setattr(hammer_vlsi, "_resolve_workspace_obj_dir", lambda *a, **k: None)
    cmds = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        return types.SimpleNamespace(returncode=0)

    context: dict = {}
    _airflow_stubs(monkeypatch, context)
    ns: dict = {}
    exec(compile(text, "hammer_dag.py", "exec"), ns)
    ns["subprocess"] = types.SimpleNamespace(run=fake_run, DEVNULL=-3)

    def run(fn: str, conf: dict, *args, task_id: str = "module_Top.syn", params=None):
        context.clear()
        context.update(
            dag_run=types.SimpleNamespace(conf=conf, dag_id="d", run_id="r"),
            ti=types.SimpleNamespace(task_id=task_id),
            task=types.SimpleNamespace(task_id=task_id, downstream_list=[]),
            params=params or {},
        )
        cmds.clear()
        ns[fn](*args, **(context if fn != "run_hammer_action" else {}))
        return cmds[0] if cmds else None

    run.ns = ns
    run.a, run.b = os.path.realpath(a), os.path.realpath(b)
    return run


class TestStepFlags:
    def _flags(self, dag, action: str, stage: str) -> list:
        cmd = dag("run_hammer_action", {"from_step": "syn_map", "steps_stage": stage}, action, ["-p", "x.json"])
        return cmd[cmd.index("--obj_dir") - 2:cmd.index("--obj_dir")] if "--from_step" in cmd else []

    def test_step_flags_reach_module_named_par(self, dag) -> None:
        assert self._flags(dag, "syn-parity_gen", "syn") == ["--from_step", "syn_map"]
        assert self._flags(dag, "par-parity_gen", "par") == ["--from_step", "syn_map"]

    def test_step_flags_skip_bridges(self, dag) -> None:
        assert self._flags(dag, "syn-to-par", "syn") == []
        assert self._flags(dag, "par-to-drc", "par") == []
        assert self._flags(dag, "hier-par-to-syn", "par") == []

    def test_step_flags_flat(self, dag) -> None:
        assert self._flags(dag, "syn", "syn") == ["--from_step", "syn_map"]
        assert self._flags(dag, "par", "syn") == []


def _configs(cmd) -> list:
    return [cmd[i + 1] for i, x in enumerate(cmd) if x == "-p"]


class TestToolsParam:
    def test_tools_param_selects_that_tools_configs(self, dag) -> None:
        cmd = dag("syn", {"syn": True, "tools": "b"}, "", dag.ns["PROJ_CONFIGS"])
        assert _configs(cmd) == [dag.b]

    def test_tools_from_params_when_conf_has_none(self, dag) -> None:
        cmd = dag("syn", {"syn": True}, "", dag.ns["PROJ_CONFIGS"], params={"tools": "b"})
        assert _configs(cmd) == [dag.b]

    def test_default_tools_command_unchanged(self, dag) -> None:
        cmd = dag("syn", {"syn": True}, "", dag.ns["PROJ_CONFIGS"])
        assert _configs(cmd) == dag.ns["PROJ_CONFIGS"] == [dag.a]

    def test_sim_rtl_follows_the_tools_choice(self, dag) -> None:
        cmd = dag("sim_rtl", {"sim_rtl": True, "tools": "b"}, "", dag.ns["PROJ_CONFIGS"], "/rundir",
                  task_id="module_Top.sim_rtl")
        assert _configs(cmd) == [dag.b]

    def test_unknown_tools_fails_task(self, dag) -> None:
        with pytest.raises(_FailException, match="zzz"):
            dag("syn", {"syn": True, "tools": "zzz"}, "", dag.ns["PROJ_CONFIGS"])

    def test_input_json_syn_ignores_tools(self, dag) -> None:
        cmd = dag("syn", {"syn": True, "tools": "b"}, "", ["/obj/syn-Top-input.json"])
        assert _configs(cmd) == ["/obj/syn-Top-input.json"]


class TestRunToolsFlag:
    def test_run_tools_flag_sets_conf(self, tmp_path, monkeypatch) -> None:
        import getpass
        from hammer.shell import sledgehammer_cli
        dags = tmp_path / "dags"
        dags.mkdir()
        (dags / f"sledgehammer_Top_{getpass.getuser()}.py").write_text("'forceall': Param(default=False)")
        calls = []

        def fake_airflow(*a, capture=True):
            calls.append(a)
            return 0, "", ""

        monkeypatch.setattr(sledgehammer_cli, "_airflow", fake_airflow)
        monkeypatch.setattr(sledgehammer_cli, "_dags_folder", lambda: (str(dags), "test"))
        assert sledgehammer_cli._cmd_run(["syn", "--tools", "or", "--obj_dir", str(tmp_path / "Top"), "--no-wait"]) == 0
        trigger = next(c for c in calls if c[:2] == ("dags", "trigger"))
        assert json.loads(trigger[trigger.index("-c") + 1]) == {"syn": True, "tools": "or"}
