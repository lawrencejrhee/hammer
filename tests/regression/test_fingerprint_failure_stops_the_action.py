import errno
import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, code_fingerprints, pd_cache, pd_store, rtl_check
from hammer.vlsi.substep_resume import MARKER_NAME


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN", raising=False)
    monkeypatch.delenv("HAMMER_SUBSTEP_RESUME", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)


def _syn(tmp_path: Path) -> int:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["syn", "-o", str(tmp_path / "output.json"), "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    return cm.value.code


@pytest.mark.parametrize("name", ["framework_fingerprint", "tool_fingerprint"])
def test_a_fingerprint_failure_changes_nothing_and_names_what_failed(tmp_path, monkeypatch, capsys, name) -> None:
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
    loads, stores = [], []
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: loads.append(key))
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda stage, key, *a, **k: stores.append(key))
    assert _syn(tmp_path) == 0
    rundir = tmp_path / "obj" / "syn-rundir"
    (rundir / "pre_step2").write_text("db")
    master = tmp_path / "obj" / "master_database.json"
    before = (master.read_bytes(), (rundir / MARKER_NAME).read_bytes())
    loads.clear()
    stores.clear()

    def broken(*args, **kwargs):
        raise OSError(errno.EIO, "Input/output error", "/nfs/tools/unreadable.py")
    monkeypatch.setattr(code_fingerprints, name, broken)
    capsys.readouterr()
    assert _syn(tmp_path) != 0
    out = capsys.readouterr().out
    key = "vlsi.framework_fingerprint_sha256" if name == "framework_fingerprint" else "synthesis.tool_fingerprint_sha256"
    assert key in out and "/nfs/tools/unreadable.py" in out
    assert "can skip syn" not in out and "Database changed" not in out
    assert (master.read_bytes(), (rundir / MARKER_NAME).read_bytes()) == before
    assert (rundir / "pre_step2").exists()
    assert loads == [] and stores == []
