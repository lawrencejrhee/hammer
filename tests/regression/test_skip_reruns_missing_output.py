import json
import shutil
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, HammerDriver, rtl_check


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


HIER = {
    "vlsi.inputs.hierarchical.top_module": "dummy",
    "vlsi.inputs.hierarchical.flat": "hierarchical",
    "vlsi.inputs.hierarchical.config_source": "manual",
    "vlsi.inputs.hierarchical.manual_modules": [{"mod1": ["m1s1"], "dummy": ["mod1"]}],
    "vlsi.inputs.hierarchical.manual_placement_constraints": [],
    "vlsi.inputs.hierarchical.constraints": [],
}


class Flow:
    def __init__(self, tmp_path: Path, capsys, **extra) -> None:
        self.tmp = tmp_path
        self.obj = tmp_path / "obj"
        self.capsys = capsys
        rtl = tmp_path / "top.v"
        rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
        (tmp_path / "mock").mkdir()
        self.cfg = {
            "vlsi.core.technology": "hammer.technology.nop",
            "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
            "vlsi.core.par_tool": "hammer.par.nop",
            "vlsi.inputs.hierarchical.config_source": "none",
            "vlsi.technology.extra_macro_sizes": [],
            "synthesis.inputs.top_module": "dummy",
            "synthesis.inputs.input_files": [str(rtl)],
            "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
            "par.skip_path_knob": 1,
            "drc.skip_path_knob": 1,
        }
        self.cfg.update(extra)
        self.write_config()

    def write_config(self) -> None:
        (self.tmp / "config.json").write_text(json.dumps(self.cfg, cls=HammerJSONEncoder, indent=4))

    def run(self, action: str, *configs: str) -> str:
        """Run one action with only the given -p configs, as make and the DAG do; returns stdout."""
        self.capsys.readouterr()
        args = [action]
        for c in configs or (str(self.tmp / "config.json"),):
            args += ["-p", c]
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=args + ["-o", str(self.tmp / f"{action}-out.json"), "--obj_dir", str(self.obj),
                                          "--log", str(self.tmp / "log.txt")])
        assert cm.value.code == 0, action
        return self.capsys.readouterr().out

    def syn_to_par_to_par(self) -> str:
        self.run("syn-to-par", str(self.obj / "syn-rundir" / "syn-output-full.json"))
        return self.run("par", str(self.tmp / "syn-to-par-out.json"))

    def full(self, stage: str, rundir: str = "") -> dict:
        return json.loads((self.obj / (rundir or f"{stage}-rundir") / f"{stage}-output-full.json").read_text())

    def needs_rerun(self, stage: str) -> bool:
        return json.loads((self.obj / "master_database.json").read_text())[f"{stage}.needsToRerun"]


class TestMissingOutputRerunsStage:
    def test_missing_syn_output_reruns_syn(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys)
        f.run("syn")
        shutil.rmtree(tmp_path / "obj" / "syn-rundir")
        f.run("syn")
        assert (tmp_path / "obj" / "syn-rundir" / "syn-output.json").is_file()
        assert f.full("syn")["par.skip_path_knob"] == 1
        assert isinstance(json.loads((tmp_path / "syn-out.json").read_text()), dict)
        assert f.needs_rerun("syn") is False
        assert "can skip syn" in f.run("syn")

    def test_syn_par_with_missing_syn_output(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys)
        f.run("syn-par")
        shutil.rmtree(tmp_path / "obj" / "syn-rundir")
        out = f.run("syn-par")
        assert "can skip par" not in out
        assert (tmp_path / "obj" / "syn-rundir" / "par-input.json").is_file()
        assert f.needs_rerun("par") is False

    def test_missing_par_output_reruns_par(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys)
        f.run("syn")
        f.syn_to_par_to_par()
        shutil.rmtree(tmp_path / "obj" / "par-rundir")
        assert "can skip par" in f.syn_to_par_to_par()
        assert (tmp_path / "obj" / "par-rundir" / "par-output.json").is_file()
        assert f.needs_rerun("par") is False

    def test_hier_missing_output_reruns_module(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys, **HIER)
        f.run("syn-m1s1")
        shutil.rmtree(tmp_path / "obj" / "syn-m1s1")
        f.run("syn-m1s1")
        module_config = json.loads((tmp_path / "obj" / "syn-m1s1" / "full_config.json").read_text())
        assert module_config["synthesis.inputs.top_module"] == "m1s1"
