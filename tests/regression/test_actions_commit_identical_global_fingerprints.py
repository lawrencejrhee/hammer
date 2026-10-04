import json
import sys
import uuid
from pathlib import Path

import pytest

from hammer.config import HammerDatabase, HammerJSONEncoder
from hammer.vlsi import CLIDriver, rtl_check

TECH_SRC = '''import os

from hammer.tech import HammerTechnology


class SharedTech(HammerTechnology):
    def post_install_script(self):
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(os.path.join(self.cache_dir, "cells.lef"), "w") as f:
            f.write("MACRO cell_a\\nEND cell_a\\n")


tech = SharedTech()
'''

GLOBAL_KEYS = ("vlsi.collateral_fingerprint_sha256", "vlsi.tech_fingerprint_sha256",
               "vlsi.framework_fingerprint_sha256")
STAGES = (("syn", "synthesis"), ("par", "par"), ("drc", "drc"), ("lvs", "lvs"))


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)
    for name in ("HAMMER_PD_CACHE", "HAMMER_AIRFLOW_DESIGN", "HAMMER_SUBSTEP_RESUME", "HAMMER_PD_COLLAT_DEBUG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


class Flow:
    """syn, par, drc and lvs on one config, with decks for the configured drc and lvs tools."""

    def __init__(self, tmp_path: Path, monkeypatch, capsys) -> None:
        self.tmp = tmp_path
        self.capsys = capsys
        self.tech = f"shared_{uuid.uuid4().hex[:8]}"
        pkg = tmp_path / "site" / self.tech
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(TECH_SRC)
        pdk = tmp_path / "pdk"
        pdk.mkdir()
        for name in ("cells.spice", "drc.rules", "lvs.rules"):
            (pdk / name).write_text("x\n")
        (pkg / f"{self.tech}.tech.json").write_text(json.dumps({
            "name": self.tech, "installs": [],
            "libraries": [{"lef_file": "cache/cells.lef", "spice_file": str(pdk / "cells.spice")}],
            "drc_decks": [{"tool_name": "mockdrc", "deck_name": "d", "path": str(pdk / "drc.rules")}],
            "lvs_decks": [{"tool_name": "mocklvs", "deck_name": "l", "path": str(pdk / "lvs.rules")}]}))
        (pkg / "defaults.yml").write_text("{}\n")
        monkeypatch.syspath_prepend(str(tmp_path / "site"))
        rtl = tmp_path / "top.v"
        rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
        (tmp_path / "mock").mkdir()
        gds = self.obj() / "par-rundir" / "dummy.gds"
        gds.parent.mkdir(parents=True)
        gds.write_text("gds\n")
        (tmp_path / "config.json").write_text(json.dumps({
            "vlsi.core.technology": self.tech,
            "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
            "vlsi.core.par_tool": "hammer.par.nop",
            "vlsi.core.drc_tool": "hammer.drc.mockdrc",
            "vlsi.core.lvs_tool": "hammer.lvs.mocklvs",
            "vlsi.inputs.hierarchical.config_source": "none",
            "vlsi.technology.extra_macro_sizes": [],
            "synthesis.inputs.top_module": "dummy",
            "synthesis.inputs.input_files": [str(rtl)],
            "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
            "drc.inputs.top_module": "dummy",
            "drc.inputs.layout_file": str(gds),
            "lvs.inputs.top_module": "dummy",
            "lvs.inputs.layout_file": str(gds),
            "lvs.inputs.schematic_files": [str(rtl)],
        }, cls=HammerJSONEncoder, indent=4))

    def obj(self) -> Path:
        return self.tmp / "obj"

    def run(self, action: str, *configs: str) -> str:
        self.capsys.readouterr()
        args = [action]
        for c in (str(self.tmp / "config.json"),) + configs:
            args += ["-p", c]
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=args + ["-o", str(self.tmp / f"{action}-out.json"), "--obj_dir", str(self.obj()),
                                          "--log", str(self.tmp / "log.txt")])
        out = self.capsys.readouterr().out
        assert cm.value.code == 0, out
        return out

    def stage(self, action: str, *configs: str) -> str:
        if action != "par":
            return self.run(action, *configs)
        self.run("syn-to-par", str(self.obj() / "syn-rundir" / "syn-output-full.json"))
        return self.run("par", str(self.tmp / "syn-to-par-out.json"), *configs)

    def master(self, names) -> dict:
        data = json.loads((self.obj() / "master_database.json").read_text())
        return {k: data.get(k) for k in names}


@pytest.fixture
def flow(tmp_path, monkeypatch, capsys):
    f = Flow(tmp_path, monkeypatch, capsys)
    yield f
    sys.modules.pop(f.tech, None)


@pytest.fixture
def checked(monkeypatch):
    """(stage, values of the global keys) at each dependency check."""
    seen = []
    real = HammerDatabase.stage_change_check

    def spy(self, stage, *args, **kwargs):
        seen.append((stage, {k: self.get_setting(k) if self.has_setting(k) else None for k in GLOBAL_KEYS}))
        return real(self, stage, *args, **kwargs)
    monkeypatch.setattr(HammerDatabase, "stage_change_check", spy)
    return seen


def test_every_action_checks_and_commits_the_global_keys_syn_computed(flow, checked) -> None:
    for action, _ in STAGES:
        flow.stage(action)
    first = checked[0][1]
    assert all(first.values())
    assert {stage for stage, _ in checked} == {action for action, _ in STAGES}
    assert all(values == first for _, values in checked)
    assert flow.master(GLOBAL_KEYS) == first
    for action, _ in STAGES:
        assert f"can skip {action}" in flow.stage(action)
    assert all(values == first for _, values in checked)


def test_stale_fingerprints_in_a_project_file_are_overwritten(flow, checked) -> None:
    for action, _ in STAGES:
        flow.stage(action)
    names = GLOBAL_KEYS + tuple(f"{tag}.{kind}_fingerprint_sha256" for _, tag in STAGES
                                for kind in ("collateral", "upstream", "tool", "hooks"))
    fresh = flow.master(names)
    assert all(fresh.values())
    stale = flow.tmp / "stale.json"
    stale.write_text(json.dumps({name: "stale" for name in names}))
    for action, _ in STAGES:
        assert f"can skip {action}" in flow.stage(action, str(stale))
    assert flow.master(names) == fresh
    assert all("stale" not in values.values() for _, values in checked)
