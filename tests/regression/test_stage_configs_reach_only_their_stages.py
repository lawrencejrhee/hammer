import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hammer.vlsi import HammerDriver, HammerDriverOptions, design_share, pd_cache, rtl_check
from hammer.vlsi.hammer_build_systems import build_airflow_dag, build_makefile

KEY = "vlsi.core.stage_configs"
DAG_NAMES = {"PROJ_CONFIGS", "PROJ_CONFS_BY_TOOLS", "DEFAULT_TOOLS", "TOOLS_CHOICES", "STAGE_CONFIGS",
             "_STAGE_ONLY_CONFIGS"}


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture(autouse=True)
def _no_slang(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)


def _files(tmp_path: Path):
    (tmp_path / "top.v").write_text("module Top(input a, output b); assign b = a; endmodule\n")
    (tmp_path / "sim-rtl.yml").write_text('sim.inputs.level: "rtl"\n')
    (tmp_path / "sim-gl-syn.yml").write_text('sim.inputs.level: "syn"\n')
    (tmp_path / "sim-gl-par.yml").write_text('sim.inputs.level: "par"\n')
    base = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.airflow_dags_folder": str(tmp_path / "dags"),
        "vlsi.inputs.hierarchical.config_source": "none",
        "synthesis.inputs.top_module": "Top",
        "synthesis.inputs.input_files": [str(tmp_path / "top.v")],
    }
    return base


def _driver(tmp_path: Path, base: dict, *more: str) -> HammerDriver:
    (tmp_path / "base.json").write_text(json.dumps(base))
    configs = [str(tmp_path / "base.json")] + [str(tmp_path / name) for name in more]
    return HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=configs,
                                            log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))


def _mapped(tmp_path: Path, base: dict) -> dict:
    return {**base, f"{KEY}.sim_rtl": [str(tmp_path / "sim-rtl.yml")],
            f"{KEY}.sim_syn": [str(tmp_path / "sim-gl-syn.yml")], f"{KEY}.sim_par": [str(tmp_path / "sim-gl-par.yml")]}


def _dag(driver: HammerDriver):
    errors = []
    build_airflow_dag(driver, errors.append)
    assert errors == []
    path = Path(driver.obj_dir) / "hammer_dag.py"
    tree = ast.parse(path.read_text())
    kept = [node for node in tree.body
            if (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in DAG_NAMES)
            or (isinstance(node, ast.FunctionDef) and node.name in ("_selected_proj_configs", "_with_stage_configs"))]
    namespace = {"AirflowFailException": RuntimeError}
    exec(compile(ast.Module(kept, []), str(path), "exec"), namespace)
    return namespace, path.read_text()


def _context(task_id: str) -> dict:
    return {"task": SimpleNamespace(task_id=task_id), "dag_run": SimpleNamespace(conf={}), "params": {}}


def _flags(paths) -> list:
    return [arg for path in paths for arg in ("-p", str(path))]


def test_each_sim_stage_reads_only_its_own_file_after_the_project_configs(tmp_path) -> None:
    base = _mapped(tmp_path, _files(tmp_path))
    dag, _ = _dag(_driver(tmp_path, base, "sim-rtl.yml", "sim-gl-syn.yml", "sim-gl-par.yml"))
    project = dag["_selected_proj_configs"](_context("module_Top.sim_rtl"))
    assert project == [str(tmp_path / "base.json")]
    flags = dag["_with_stage_configs"](_flags(project) + ["--sim_rundir", "r"], _context("module_Top.sim_rtl"))
    assert flags == _flags([tmp_path / "base.json", tmp_path / "sim-rtl.yml"]) + ["--sim_rundir", "r"]


def test_a_gate_level_stage_reads_its_file_before_the_generated_input(tmp_path) -> None:
    dag, _ = _dag(_driver(tmp_path, _mapped(tmp_path, _files(tmp_path)), "sim-gl-syn.yml"))
    flags = dag["_with_stage_configs"](["-p", "in.json", "--sim_rundir", "r"], _context("module_Top.sim_syn"))
    assert flags == _flags([tmp_path / "sim-gl-syn.yml", "in.json"]) + ["--sim_rundir", "r"]


def test_a_stage_with_no_files_is_left_alone(tmp_path) -> None:
    dag, _ = _dag(_driver(tmp_path, _mapped(tmp_path, _files(tmp_path))))
    flags = ["-p", "syn-output.json", "-o", "par-input.json"]
    assert dag["_with_stage_configs"](flags, _context("module_Top.syn_to_par")) == flags


def test_without_the_keys_every_stage_gets_every_config_as_before(tmp_path) -> None:
    base = _files(tmp_path)
    dag, _ = _dag(_driver(tmp_path, base, "sim-rtl.yml", "sim-gl-par.yml"))
    assert dag["STAGE_CONFIGS"] == {}
    expected = [str(tmp_path / n) for n in ("base.json", "sim-rtl.yml", "sim-gl-par.yml")]
    assert dag["_selected_proj_configs"](_context("module_Top.sim_rtl")) == expected


def test_the_dag_keeps_the_full_list_a_shared_design_bundles(tmp_path) -> None:
    dag, _ = _dag(_driver(tmp_path, _mapped(tmp_path, _files(tmp_path)), "sim-rtl.yml"))
    assert str(tmp_path / "sim-rtl.yml") in dag["PROJ_CONFS_BY_TOOLS"]["default"]


def test_the_lvs_lines_a_course_tool_patches_stay_as_they_were(tmp_path) -> None:
    _, text = _dag(_driver(tmp_path, _files(tmp_path)))
    lines = text.splitlines()
    assert '        run_hammer_action("par-to-lvs", ["-p", par_out, "-o", lvs_in])' in lines
    assert '        run_hammer_action(f"lvs{suffix}", ["-p", lvs_in])' in lines


def test_a_relative_path_resolves_against_the_yml_that_names_it(tmp_path) -> None:
    base = _files(tmp_path)
    (tmp_path / "stages.yml").write_text(f'{KEY}.sim_rtl: ["sim-rtl.yml"]\n{KEY}.sim_rtl_meta: prependlocal\n')
    dag, _ = _dag(_driver(tmp_path, base, "stages.yml"))
    assert dag["STAGE_CONFIGS"] == {"sim_rtl": [str((tmp_path / "sim-rtl.yml").resolve())]}


def test_hammer_d_gives_each_recipe_the_same_files(tmp_path) -> None:
    driver = _driver(tmp_path, _mapped(tmp_path, _files(tmp_path)), "sim-rtl.yml", "sim-gl-syn.yml", "sim-gl-par.yml")
    errors = []
    build_makefile(driver, errors.append)
    assert errors == []
    recipes = (Path(driver.obj_dir) / "hammer.d").read_text().splitlines()
    base, rtl, gl_syn = (tmp_path / "base.json", tmp_path / "sim-rtl.yml", tmp_path / "sim-gl-syn.yml")
    sim_rtl = next(r for r in recipes if "--sim_rundir" in r and "sim-rtl-rundir" in r)
    assert f"-p {base} -p {rtl} $(HAMMER_EXTRA_ARGS)" in sim_rtl
    sim_syn = next(r for r in recipes if "--sim_rundir" in r and "sim-syn-rundir" in r)
    assert f"-p {gl_syn} -p {tmp_path / 'obj' / 'sim-syn-input.json'} $(HAMMER_EXTRA_ARGS)" in sim_syn
    syn = next(r for r in recipes if r.endswith(" syn"))
    assert str(rtl) not in syn and f"-p {base} $(HAMMER_EXTRA_ARGS)" in syn


@pytest.mark.parametrize("key, value, expected", [
    (f"{KEY}.simrtl", ["x.yml"], "no such stage"),
    (f"{KEY}.sim_rtl", ["/no/such/file.yml"], "no such file"),
])
def test_a_wrong_stage_or_missing_file_is_an_error(tmp_path, key, value, expected) -> None:
    errors = []
    build_makefile(_driver(tmp_path, {**_files(tmp_path), key: value}), errors.append)
    assert any(expected in e for e in errors)


@pytest.mark.parametrize("stage", ["synthesis", "par"])
def test_the_mapping_does_not_change_syn_or_par_cache_keys(tmp_path, stage) -> None:
    base = _files(tmp_path)
    without = pd_cache._build_cache_key(_driver(tmp_path, base, "sim-rtl.yml", "sim-gl-par.yml"), stage)
    with_map = pd_cache._build_cache_key(_driver(tmp_path, _mapped(tmp_path, base)), stage)
    assert with_map == without


def test_a_stage_file_named_only_by_the_key_is_bundled(tmp_path) -> None:
    base = _files(tmp_path)
    (tmp_path / "base.json").write_text(json.dumps(base))
    config = {f"{KEY}.lvs": [str(tmp_path / "sim-rtl.yml")]}
    bundle = design_share.build_bundle("d", str(tmp_path), str(tmp_path / "obj"), [],
                                       {"default": [str(tmp_path / "base.json")]}, "default", None, config)
    assert "work/sim-rtl.yml" in bundle.manifest["files"]
