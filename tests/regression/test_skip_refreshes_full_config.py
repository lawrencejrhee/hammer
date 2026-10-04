import json
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


class TestSkippedStageRefreshesFullConfig:
    def test_skipped_syn_refreshes_full_config(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys)
        f.run("syn")
        before = f.full("syn")
        f.cfg["par.skip_path_knob"] = 2
        f.write_config()
        assert "can skip syn" in f.run("syn")
        assert f.full("syn") == dict(before, **{"par.skip_path_knob": 2})

    def test_par_only_edit_reaches_par(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys)
        f.run("syn")
        f.syn_to_par_to_par()
        f.cfg["par.skip_path_knob"] = 2
        f.write_config()
        assert "can skip syn" in f.run("syn")
        out = f.syn_to_par_to_par()
        assert "can skip par" not in out
        assert json.loads((tmp_path / "syn-to-par-out.json").read_text())["par.skip_path_knob"] == 2
        assert f.full("par")["par.skip_path_knob"] == 2

    def test_unchanged_skip_keeps_full_config_mtime(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys)
        f.run("syn")
        full = tmp_path / "obj" / "syn-rundir" / "syn-output-full.json"
        before = full.stat().st_mtime_ns
        assert "can skip syn" in f.run("syn")
        assert full.stat().st_mtime_ns == before

    def test_skipped_par_refreshes_full_config_for_drc_edit(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys)
        f.run("syn")
        f.syn_to_par_to_par()
        f.cfg["drc.skip_path_knob"] = 2
        f.write_config()
        assert "can skip syn" in f.run("syn")
        assert "can skip par" in f.syn_to_par_to_par()
        assert f.full("par")["drc.skip_path_knob"] == 2

    def test_hier_skip_full_config_matches_run(self, tmp_path, capsys) -> None:
        f = Flow(tmp_path, capsys, **HIER)
        f.run("syn-m1s1")
        before = f.full("syn", "syn-m1s1")
        assert before.get("vlsi.inputs.hierarchical.mode") != "leaf"
        f.cfg["par.skip_path_knob"] = 2
        f.write_config()
        assert "can skip syn" in f.run("syn-m1s1")
        assert f.full("syn", "syn-m1s1") == dict(before, **{"par.skip_path_knob": 2})

    def test_skip_with_output_present_still_skips(self, tmp_path, capsys, monkeypatch) -> None:
        f = Flow(tmp_path, capsys)
        f.run("syn")

        def must_not_run(*a, **k):
            raise AssertionError("synthesis ran on an unchanged stage")
        monkeypatch.setattr(HammerDriver, "run_synthesis", must_not_run)
        assert "can skip syn" in f.run("syn")
