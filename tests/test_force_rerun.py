import ast
import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, HammerDriver, HammerDriverOptions
from hammer.vlsi import pd_cache, pd_store


@pytest.fixture(autouse=True)
def _no_shared_state(monkeypatch):
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


def _syn_par(tmp_path: Path) -> None:
    obj = tmp_path / "obj"
    assert _run(tmp_path, "syn") == 0
    assert _run(tmp_path, "syn-to-par", "-p", str(obj / "syn-rundir" / "syn-output-full.json"),
                "-o", str(obj / "par-input.json")) == 0
    assert _run(tmp_path, "par", "-p", str(obj / "par-input.json")) == 0


def _needs_rerun(tmp_path: Path) -> dict:
    master = json.loads((tmp_path / "obj" / "master_database.json").read_text())
    return {k.split(".")[0]: v for k, v in master.items() if k.endswith(".needsToRerun")}


def _driver(tmp_path: Path, cfg: str) -> HammerDriver:
    return HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=[cfg],
                                            log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))


@pytest.fixture
def cache_spies(monkeypatch):
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    loads, stores = [], []
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: loads.append(key))
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda stage, key, *a, **k: stores.append(key))
    monkeypatch.setattr(pd_cache, "_legacy_lookup", lambda *a, **k: (None, None))
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)
    return loads, stores


class TestStageChangeCheck:
    def test_force_rebuilds_the_pending_master_and_marks_successors(self, tmp_path) -> None:
        db = _driver(tmp_path, _config(tmp_path)).database
        master = str(tmp_path / "master.json")
        assert db.stage_change_check("syn", master)
        db.commit_master_database()
        assert not db.stage_change_check("syn", master)
        assert db._pending_master_db is None
        assert db.stage_change_check("syn", master, force=True)
        pending = json.loads(db._pending_master_db[1])
        assert pending["syn.needsToRerun"] is False
        assert all(pending[f"{s}.needsToRerun"] is True for s in ("par", "drc", "lvs"))


class TestForcedRun:
    def test_forced_syn_clears_its_flag_and_marks_par(self, tmp_path, capsys) -> None:
        _config(tmp_path)
        _syn_par(tmp_path)
        assert _needs_rerun(tmp_path)["par"] is False
        assert _run(tmp_path, "syn", "--force") == 0
        flags = _needs_rerun(tmp_path)
        assert flags["syn"] is False
        assert flags["par"] is True
        capsys.readouterr()
        assert _run(tmp_path, "syn") == 0
        assert "Database unchanged, can skip syn" in capsys.readouterr().out

    def test_force_skips_the_cache_lookup_and_overwrites(self, tmp_path, cache_spies) -> None:
        loads, stores = cache_spies
        _config(tmp_path)
        assert _run(tmp_path, "syn") == 0
        assert len(loads) == 1 and len(stores) == 1
        assert _run(tmp_path, "syn", "--force") == 0
        assert len(loads) == 1
        assert stores == [stores[0], stores[0]]

    def test_step_flag_runs_are_never_stored(self, tmp_path, cache_spies) -> None:
        loads, stores = cache_spies
        _config(tmp_path)
        _run(tmp_path, "syn", "--to_step", "step2")
        assert stores == []
        _run(tmp_path, "syn", "--force", "--to_step", "step2")
        assert stores == []
        assert len(loads) == 1


def _generated_dag(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setenv("HAMMER_DAGS_FOLDER", str(tmp_path / "dags"))
    cfg = _config(tmp_path, **{"vlsi.core.build_system": "sledgehammer"})
    CLIDriver.generate_build_inputs(_driver(tmp_path, cfg), lambda x: None)
    return (tmp_path / "obj" / "hammer_dag.py").read_text()


def _generated_force_requested(tmp_path: Path, monkeypatch):
    tree = ast.parse(_generated_dag(tmp_path, monkeypatch))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_force_requested")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "hammer_dag.py", "exec"), ns)
    return ns["_force_requested"]


class TestDagForce:
    def test_redo_forces_only_the_selected_stage(self, tmp_path, monkeypatch) -> None:
        force = _generated_force_requested(tmp_path, monkeypatch)
        conf = {"par": True, "redo": True}
        assert force(conf, "module_Top.par", True)
        assert not force(conf, "module_Top.syn", True)
        assert not force(conf, "module_Top.par", False)
        assert not force({"par": True}, "module_Top.par", True)

    def test_forceall_forces_every_stage(self, tmp_path, monkeypatch) -> None:
        force = _generated_force_requested(tmp_path, monkeypatch)
        conf = {"par": True, "redo": True, "forceall": True}
        assert force(conf, "module_Top.syn", True)
        assert force(conf, "module_Top.par", True)

    def test_generated_dag_forces_per_stage_without_importing_hammer(self, tmp_path, monkeypatch) -> None:
        text = _generated_dag(tmp_path, monkeypatch)
        assert "'forceall': Param(" in text
        assert "_force = _force_requested(_mods, _task.task_id," in text
        assert '"-to-" not in action_clean' in text
        assert "import dag_force_requested" not in text
        assert "if _mods.get('redo'):" not in text
