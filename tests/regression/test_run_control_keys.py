import json
import os
from pathlib import Path

import pytest

from hammer.config.config_src import RUN_CONTROL_KEYS
from hammer.vlsi import HammerDriver, HammerDriverOptions, pd_cache, pd_store, sledge_settings
from hammer.vlsi.hammer_build_systems import build_airflow_dag


def _driver(tmp_path: Path, **extra) -> HammerDriver:
    rtl = tmp_path / "top.v"
    rtl.write_text("module Top(input a, output b); assign b = a; endmodule\n")
    cfg = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "synthesis.inputs.top_module": "Top",
        "synthesis.inputs.input_files": [str(rtl)],
    }
    cfg.update(extra)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    return HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=[str(path)],
                                            log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))


def test_every_run_control_key_has_a_typed_default(tmp_path) -> None:
    db = _driver(tmp_path).database
    for key in sorted(RUN_CONTROL_KEYS):
        assert key in db.defaults, key
        assert key in db.get_config_types, key
        assert db.check_setting(key), key


def test_defaults_match_todays_behaviour(tmp_path, monkeypatch) -> None:
    for var in ("HAMMER_PD_CACHE", "HAMMER_PD_CACHE_LEDGER"):
        monkeypatch.delenv(var, raising=False)
    d = _driver(tmp_path)
    assert pd_cache.is_cache_enabled(d) is False
    assert sledge_settings.flag(d, "vlsi.pd_cache.ledger_enabled", None) is True
    assert sledge_settings.flag(d, "vlsi.substep_resume.enabled", None) is True
    assert sledge_settings.flag(d, "vlsi.error_scan.enabled", None) is True
    assert sledge_settings.flag(d, "vlsi.core.airflow_edge", None) is False
    assert sledge_settings.text(d, "vlsi.core.airflow_queue") is None


@pytest.mark.parametrize("value,expected", [
    ("false", False), ("OFF", False), (0, False), ("no", False),
    ("true", True), ("Yes", True), (1, True), (True, True),
])
def test_flag_spellings(tmp_path, value, expected) -> None:
    d = _driver(tmp_path, **{"vlsi.core.airflow_edge": value})
    assert sledge_settings.flag(d, "vlsi.core.airflow_edge", not expected) is expected


def test_cache_on_with_integer_one(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    assert pd_cache.is_cache_enabled(_driver(tmp_path, **{"vlsi.pd_cache.enabled": 1})) is True


def _edge_queue(tmp_path, monkeypatch, **extra) -> str:
    monkeypatch.setenv("HAMMER_DAGS_FOLDER", str(tmp_path / "dags"))
    monkeypatch.setenv("USER", "alice")
    build_airflow_dag(_driver(tmp_path, **extra), lambda e: None)
    text = (tmp_path / "obj" / "hammer_dag.py").read_text()
    return next(line for line in text.splitlines() if line.strip().startswith("EDGE_QUEUE ="))


def test_edge_string_false_stays_local(tmp_path, monkeypatch) -> None:
    assert _edge_queue(tmp_path, monkeypatch, **{"vlsi.core.airflow_edge": "false"}).strip() == "EDGE_QUEUE = None"


def test_edge_null_queue_uses_the_user_not_none(tmp_path, monkeypatch) -> None:
    line = _edge_queue(tmp_path, monkeypatch, **{"vlsi.core.airflow_edge": True, "vlsi.core.airflow_queue": None})
    assert line.strip() == "EDGE_QUEUE = 'alice'"


def test_run_control_keys_never_move_a_cache_key() -> None:
    base = {"synthesis.inputs.top_module": "Top", "vlsi.core.technology": "x"}
    noisy = dict(base, **{k: "anything" for k in RUN_CONTROL_KEYS})
    for stage in ("synthesis", "par"):
        assert pd_store.compute_stage_key(base, stage) == pd_store.compute_stage_key(noisy, stage)


def test_setting_a_run_control_key_does_not_rerun(tmp_path) -> None:
    master = str(tmp_path / "master.json")
    db = _driver(tmp_path).database
    assert db.stage_change_check("syn", master)
    db.commit_master_database()
    db = _driver(tmp_path, **{"vlsi.pd_cache.project": "tapeout-a", "vlsi.error_scan.ignore": ["IMPLF-24"]}).database
    assert not db.stage_change_check("syn", master)


def test_numeric_labels_still_work(tmp_path, monkeypatch) -> None:
    from hammer.vlsi import time_tracking
    # Record the original first, so teardown also removes the value the stamp writes.
    monkeypatch.setenv("HAMMER_PD_PROJECT", "")
    monkeypatch.delenv("HAMMER_PD_PROJECT")
    d = _driver(tmp_path, **{"vlsi.pd_cache.project": 2026, "vlsi.core.airflow_dag_id": 7})
    time_tracking.stamp_project_from_config(d)
    assert os.environ["HAMMER_PD_PROJECT"] == "2026"
    assert sledge_settings.text(d, "vlsi.core.airflow_dag_id") == "7"
