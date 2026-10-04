import json
import os
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, fingerprints, pd_cache, pd_store, rtl_check


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture
def stores(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)
    for name in ("HAMMER_AIRFLOW_DESIGN", "HAMMER_SUBSTEP_RESUME", "HAMMER_PD_COLLAT_DEBUG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: None)
    found = []
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda stage, key, *a, **k: found.append((stage, key)))
    return found


def _par(tmp_path: Path, obj: Path, settings: dict, *extra: str) -> None:
    cfg = tmp_path / f"{obj.name}.json"
    cfg.write_text(json.dumps(dict({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "par.inputs.top_module": "dummy",
    }, **settings), cls=HammerJSONEncoder, indent=4))
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["par", "-p", str(cfg), "-o", str(tmp_path / "par-out.json"), "--obj_dir", str(obj),
                               "--log", str(tmp_path / "log.txt")] + list(extra))
    assert cm.value.code == 0


def test_a_file_in_the_par_rundir_keys_par_by_its_content(tmp_path, stores) -> None:
    netlist = tmp_path / "top.v"
    netlist.write_text("module dummy; endmodule\n")
    for name, text in (("objA", "set a 1\n"), ("objB", "set a 1\n"), ("objC", "set a 2\n")):
        script = tmp_path / name / "par-rundir" / "floorplan.tcl"
        script.parent.mkdir(parents=True)
        script.write_text(text)
        _par(tmp_path, tmp_path / name, {"par.inputs.input_files": [str(netlist)],
                                         "par.inputs.floorplan_script": str(script)})
    assert [stage for stage, _ in stores] == ["par"] * 3
    assert stores[0] == stores[1]
    assert stores[2] != stores[0]


def test_a_netlist_inside_a_nested_par_rundir_changes_the_par_key(tmp_path, stores) -> None:
    obj = tmp_path / "obj"
    netlist = obj / "stages" / "syn-rundir" / "top.mapped.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text("module dummy; endmodule\n")
    settings = {"par.inputs.input_files": [str(netlist)]}
    rundir = ["--par_rundir", str(obj / "stages")]
    _par(tmp_path, obj, settings, *rundir)
    netlist.write_text("module dummy; wire w; endmodule\n")
    _par(tmp_path, obj, settings, *(rundir + ["--force"]))
    assert [stage for stage, _ in stores] == ["par", "par"]
    assert stores[0] != stores[1]


def test_a_restored_upstream_file_too_big_to_hash_keeps_the_par_key(tmp_path, stores, monkeypatch) -> None:
    monkeypatch.setattr(fingerprints, "CONTENT_HASH_LIMIT", 16)
    netlist = tmp_path / "objA" / "syn-rundir" / "top.mapped.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text("module dummy; endmodule\n")
    os.utime(netlist, ns=(1791091939649747839, 1791091939649747839))
    pd_store.untar_to_directory(pd_store.tar_directory(netlist.parent), tmp_path / "objB")
    for name in ("objA", "objB"):
        obj = tmp_path / name
        _par(tmp_path, obj, {"par.inputs.input_files": [str(obj / "syn-rundir" / "top.mapped.v")]})
    assert [stage for stage, _ in stores] == ["par", "par"]
    assert stores[0] == stores[1]
