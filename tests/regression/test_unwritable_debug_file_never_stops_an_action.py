import json
import re

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, code_fingerprints as cf, fingerprints as fp, rtl_check


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture
def unwritable(tmp_path, monkeypatch):
    out = tmp_path / "missing-dir" / "collat.txt"
    monkeypatch.setenv(fp.DEBUG_ENV, str(out))
    monkeypatch.setattr(fp, "_DEBUG_WARNED", set())
    return out


def test_a_debug_path_in_a_missing_dir_warns_once_and_keys_still_compute(unwritable, capsys):
    fp.debug_record("vlsi.collateral", ["a"])
    assert re.fullmatch(r"[0-9a-f]{64}", cf.framework_fingerprint())
    assert not unwritable.exists()
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1 and fp.DEBUG_ENV in err and str(unwritable) in err


def test_syn_runs_with_a_debug_path_it_cannot_write(tmp_path, monkeypatch, unwritable):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)
    for name in ("HAMMER_PD_CACHE", "HAMMER_AIRFLOW_DESIGN", "HAMMER_SUBSTEP_RESUME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    rtl = tmp_path / "top.v"
    rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (tmp_path / "mock").mkdir()
    (tmp_path / "config.json").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(rtl)],
        "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
    }, cls=HammerJSONEncoder))
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["syn", "-o", str(tmp_path / "output.json"), "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    assert cm.value.code == 0
    db = json.loads((tmp_path / "obj" / "master_database.json").read_text())
    assert re.fullmatch(r"[0-9a-f]{64}", db["synthesis.hooks_fingerprint_sha256"])


def test_a_writable_debug_path_still_gets_every_record(tmp_path, monkeypatch):
    out = tmp_path / "collat.txt"
    monkeypatch.setenv(fp.DEBUG_ENV, str(out))
    fp.debug_record("par.upstream", ["b", "a"])
    assert out.read_text() == "par.upstream\ta\npar.upstream\tb\n"
