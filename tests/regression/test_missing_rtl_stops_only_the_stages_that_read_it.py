import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, rtl_check


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in ("HAMMER_PD_CACHE", "HAMMER_AIRFLOW_DESIGN", "HAMMER_SUBSTEP_RESUME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


class Flow:
    def __init__(self, tmp_path: Path, capsys, **extra) -> None:
        self.tmp = tmp_path
        self.obj = tmp_path / "obj"
        self.capsys = capsys
        self.rtl = tmp_path / "top.v"
        self.rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
        (tmp_path / "config.json").write_text(json.dumps({
            "vlsi.core.technology": "hammer.technology.nop",
            "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
            "vlsi.core.par_tool": "hammer.par.nop",
            "vlsi.inputs.hierarchical.config_source": "none",
            "vlsi.technology.extra_macro_sizes": [],
            "synthesis.inputs.top_module": "dummy",
            "synthesis.inputs.input_files": [str(self.rtl)],
            **extra,
        }, cls=HammerJSONEncoder, indent=4))

    def run(self, action: str, config: str) -> tuple:
        self.capsys.readouterr()
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=[action, "-p", config, "-o", str(self.tmp / f"{action}-out.json"),
                                   "--obj_dir", str(self.obj), "--log", str(self.tmp / "log.txt")])
        captured = self.capsys.readouterr()
        return cm.value.code, captured.out + captured.err


def test_par_runs_after_the_rtl_is_gone(tmp_path, capsys) -> None:
    """Par reads syn's outputs, not the RTL, so it runs after the RTL is cleaned away."""
    f = Flow(tmp_path, capsys)
    assert f.run("syn", str(tmp_path / "config.json"))[0] == 0
    assert f.run("syn-to-par", str(f.obj / "syn-rundir" / "syn-output-full.json"))[0] == 0
    assert f.run("par", str(tmp_path / "syn-to-par-out.json"))[0] == 0
    master = f.obj / "master_database.json"
    recorded = json.loads(master.read_text())["vlsi.rtl_fingerprint_sha256"]
    rtl_text = f.rtl.read_text()
    f.rtl.unlink()
    code, out = f.run("par", str(tmp_path / "syn-to-par-out.json"))
    assert code == 0, out
    assert "RTL file not found" in out and "recorded in master_database.json" in out
    assert "can skip par" in out
    db = json.loads(master.read_text())
    assert db["vlsi.rtl_fingerprint_sha256"] == recorded
    assert db.get("syn.needsToRerun") is not True
    f.rtl.write_text(rtl_text)
    assert "can skip syn" in f.run("syn", str(tmp_path / "config.json"))[1]


def test_a_later_stage_without_a_recorded_fingerprint_runs_without_one(tmp_path, capsys) -> None:
    f = Flow(tmp_path, capsys)
    assert f.run("syn", str(tmp_path / "config.json"))[0] == 0
    assert f.run("syn-to-par", str(f.obj / "syn-rundir" / "syn-output-full.json"))[0] == 0
    f.rtl.unlink()
    (f.obj / "master_database.json").unlink()
    code, out = f.run("par", str(tmp_path / "syn-to-par-out.json"))
    assert code == 0, out
    assert "without the RTL fingerprint" in out


def test_syn_still_fails_without_its_rtl(tmp_path, capsys) -> None:
    f = Flow(tmp_path, capsys)
    f.rtl.unlink()
    code, out = f.run("syn", str(tmp_path / "config.json"))
    assert code == 1
    assert "RTL file not found" in out


def test_par_skips_without_its_rtl_under_a_byte_fingerprint_with_include_dirs(tmp_path, capsys, monkeypatch) -> None:
    def no_slang(*args, **kwargs):
        raise rtl_check.SlangNotFound("slang is not installed")
    monkeypatch.setattr(rtl_check, "digest_units", no_slang)
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "defs.vh").write_text("`define W 1\n")
    f = Flow(tmp_path, capsys, **{"synthesis.inputs.include_dirs": [str(inc)]})
    assert f.run("syn", str(tmp_path / "config.json"))[0] == 0
    assert f.run("syn-to-par", str(f.obj / "syn-rundir" / "syn-output-full.json"))[0] == 0
    assert f.run("par", str(tmp_path / "syn-to-par-out.json"))[0] == 0
    rtl_text = f.rtl.read_text()
    f.rtl.unlink()
    code, out = f.run("par", str(tmp_path / "syn-to-par-out.json"))
    assert code == 0, out
    assert "can skip par" in out
    assert json.loads((f.obj / "master_database.json").read_text()).get("syn.needsToRerun") is not True
    f.rtl.write_text(rtl_text)
    assert "can skip syn" in f.run("syn", str(tmp_path / "config.json"))[1]


def test_rtl_sim_still_fails_without_its_rtl(tmp_path, capsys) -> None:
    f = Flow(tmp_path, capsys, **{"vlsi.core.sim_tool": "hammer.sim.mocksim", "sim.inputs.top_module": "dummy",
                                  "sim.inputs.level": "rtl", "sim.inputs.input_files": [str(tmp_path / "top.v")],
                                  "sim.inputs.options": []})
    f.rtl.unlink()
    code, out = f.run("sim", str(tmp_path / "config.json"))
    assert code == 1
    assert "RTL file not found" in out
