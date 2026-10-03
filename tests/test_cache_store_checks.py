import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, HammerDriver, HammerDriverOptions
from hammer.vlsi import error_scan, pd_cache, pd_store


@pytest.fixture
def spies(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN", raising=False)
    loads, stores = [], []
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: loads.append(key))
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda stage, key, *a, **k: stores.append(key))
    monkeypatch.setattr(pd_cache, "_legacy_lookup", lambda *a, **k: (None, None))
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)
    rtl = tmp_path / "top.v"
    rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (tmp_path / "mock").mkdir()
    (tmp_path / "config.json").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(rtl)],
        "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
    }, cls=HammerJSONEncoder))
    return loads, stores


def _syn(tmp_path: Path, *flags: str) -> int:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["syn", *flags, "-o", str(tmp_path / "output.json"),
                               "-p", str(tmp_path / "config.json"), "--obj_dir", str(tmp_path / "obj"),
                               "--log", str(tmp_path / "log.txt")])
    return cm.value.code


def test_run_failing_the_error_scan_is_not_stored(tmp_path, monkeypatch, spies) -> None:
    loads, stores = spies
    scans = []
    monkeypatch.setattr(error_scan, "scan_and_report",
                        lambda *a, **k: scans.append(a) or {"total": 1, "ignored": 0, "by_code": {"X-1": 1},
                                                             "fatal": ["X-1"]})
    assert _syn(tmp_path) != 0
    assert stores == []
    assert len(scans) == 1


def test_clean_run_is_still_stored_and_scanned_once(tmp_path, monkeypatch, spies) -> None:
    loads, stores = spies
    scans = []
    monkeypatch.setattr(error_scan, "scan_and_report", lambda *a, **k: scans.append(a) or None)
    assert _syn(tmp_path) == 0
    assert len(stores) == 1
    assert len(scans) == 1


def test_step_flag_run_skips_the_cache_lookup(tmp_path, spies) -> None:
    loads, stores = spies
    assert _syn(tmp_path) == 0
    assert len(loads) == 1
    _syn(tmp_path, "--to_step", "step2")
    assert len(loads) == 1


def test_a_raising_check_keeps_the_run_out_of_the_cache(tmp_path, spies) -> None:
    loads, stores = spies
    driver = HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=[str(tmp_path / "config.json")],
                                              log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))

    def boom() -> bool:
        raise RuntimeError("boom")

    rundir = tmp_path / "obj" / "syn-rundir"
    assert pd_cache.cache_or_run(driver, "synthesis", str(rundir), "syn-output.json",
                                 run_fn=lambda: (True, {}), accept=boom) == (True, {})
    assert stores == []
