import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in ("HAMMER_PD_CACHE", "HAMMER_PD_CACHE_LEDGER", "HAMMER_SUBSTEP_RESUME", "HAMMER_DB_CHECKPOINTS",
                 "HAMMER_CHECKPOINT_STREAM", "HAMMER_ERROR_SCAN", "HAMMER_AIRFLOW_DESIGN", "HAMMER_AIRFLOW_RUN_ID",
                 "AIRFLOW_CTX_DAG_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


class Flow:
    """One obj_dir whose stages run under build_system make or hammer, syn from a.v or b.v."""

    def __init__(self, tmp_path: Path, capsys) -> None:
        self.tmp = tmp_path
        self.obj = tmp_path / "obj"
        self.capsys = capsys
        for name, body in (("a.v", "assign b = a;"), ("b.v", "assign b = ~a;")):
            (tmp_path / name).write_text(f"module dummy(input a, output b); {body} endmodule\n")
        (tmp_path / "dummy.gds").write_text("gds\n")

    def syn(self, rtl: str, build_system: str) -> str:
        config = self.tmp / f"{rtl}-{build_system}.json"
        config.write_text(json.dumps({
            "vlsi.core.technology": "hammer.technology.nop",
            "vlsi.core.build_system": build_system,
            "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
            "vlsi.inputs.hierarchical.config_source": "none",
            "vlsi.technology.extra_macro_sizes": [],
            "synthesis.inputs.top_module": "dummy",
            "synthesis.inputs.input_files": [str(self.tmp / rtl)],
        }, cls=HammerJSONEncoder, indent=4))
        return self.run("syn", config)

    def stage(self, stage: str, build_system: str) -> str:
        config = self.tmp / f"{stage}-{build_system}.json"
        config.write_text(json.dumps({
            "vlsi.core.technology": "hammer.technology.nop",
            "vlsi.core.build_system": build_system,
            "vlsi.core.par_tool": "hammer.par.mockpar",
            "vlsi.core.drc_tool": "hammer.drc.mockdrc",
            "vlsi.core.lvs_tool": "hammer.lvs.mocklvs",
            "vlsi.inputs.hierarchical.config_source": "none",
            "vlsi.technology.extra_macro_sizes": [],
            "par.inputs.top_module": "dummy",
            "par.inputs.input_files": [str(self.tmp / "a.v")],
            "drc.inputs.top_module": "dummy",
            "drc.inputs.layout_file": str(self.tmp / "dummy.gds"),
            "lvs.inputs.top_module": "dummy",
            "lvs.inputs.layout_file": str(self.tmp / "dummy.gds"),
            "lvs.inputs.schematic_files": [str(self.tmp / "a.v")],
        }, cls=HammerJSONEncoder, indent=4))
        return self.run(stage, config)

    def run(self, action: str, config: Path) -> str:
        self.capsys.readouterr()
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=[action, "-p", str(config), "-o", str(self.tmp / f"{action}-out.json"),
                                   "--obj_dir", str(self.obj), "--log", str(self.tmp / "log.txt")])
        out = self.capsys.readouterr().out
        assert cm.value.code == 0, out
        return out

    def synthesized(self) -> list:
        output = json.loads((self.obj / "syn-rundir" / "syn-output.json").read_text())
        return [Path(p).name for p in output["synthesis.inputs.input_files"]]


def test_switching_back_from_hammer_reruns_the_stage_it_rewrote(tmp_path, capsys) -> None:
    """make a.v, hammer b.v, make a.v: the last run synthesizes a.v again instead of handing out b.v's synthesis."""
    f = Flow(tmp_path, capsys)
    f.syn("a.v", "make")
    f.syn("b.v", "hammer")
    assert f.synthesized() == ["b.v"]
    assert json.loads((f.obj / "master_database.json").read_text())["syn.needsToRerun"] is True
    out = f.syn("a.v", "make")
    assert "can skip syn" not in out
    assert f.synthesized() == ["a.v"]
    assert "can skip syn" in f.syn("a.v", "make")


@pytest.mark.parametrize("stage", ["par", "drc", "lvs"])
def test_switching_back_from_hammer_reruns_par_drc_and_lvs(tmp_path, capsys, stage) -> None:
    """make, hammer, make on unchanged inputs: the last run reruns instead of returning hammer mode's output."""
    f = Flow(tmp_path, capsys)
    f.stage(stage, "make")
    f.stage(stage, "hammer")
    assert f"can skip {stage}" not in f.stage(stage, "make")
    assert f"can skip {stage}" in f.stage(stage, "make")


def test_hammer_mode_removes_an_earlier_resume_marker(tmp_path, capsys) -> None:
    f = Flow(tmp_path, capsys)
    f.syn("a.v", "make")
    marker = f.obj / "syn-rundir" / ".substep_resume.json"
    marker.write_text("{}")
    f.syn("b.v", "hammer")
    assert not marker.exists()


def test_hammer_mode_still_creates_no_master_database(tmp_path, capsys) -> None:
    f = Flow(tmp_path, capsys)
    f.syn("a.v", "hammer")
    f.syn("b.v", "hammer")
    assert not (f.obj / "master_database.json").exists()
