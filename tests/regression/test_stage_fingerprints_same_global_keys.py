import errno
import json
import sys
import uuid
from pathlib import Path

import pytest

from hammer.vlsi import CLIDriver, HammerDriver, HammerDriverOptions
from hammer.vlsi import code_fingerprints, fingerprints as fp

TECH_SRC = '''import os

from hammer.tech import HammerTechnology
from hammer.vlsi import HammerTool


def drc_fix(ht: HammerTool) -> bool:
    ht.append("drc fix")
    return True


class GetterTech(HammerTechnology):
    def post_install_script(self):
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(os.path.join(self.cache_dir, "cells.lef"), "w") as f:
            f.write("MACRO cell\\nEND cell\\n")

    def get_tech_drc_hooks(self, tool_name):
        self.config.grid_unit = "0.001"
        return [HammerTool.make_pre_insertion_hook("run_drc", drc_fix)]


tech = GetterTech()
'''

GLOBAL_KEYS = {"vlsi.collateral_fingerprint_sha256", "vlsi.tech_fingerprint_sha256",
               "vlsi.framework_fingerprint_sha256"}
ACTIONS = (("syn", "synthesis"), ("par", "par"), ("drc", "drc"), ("lvs", "lvs"))


@pytest.fixture(autouse=True)
def _no_debug_output(monkeypatch):
    monkeypatch.delenv("HAMMER_PD_COLLAT_DEBUG", raising=False)


def _file(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def design(tmp_path, monkeypatch):
    name = f"getter_{uuid.uuid4().hex[:8]}"
    pkg = tmp_path / "site" / name
    pdk = tmp_path / "pdk"
    obj = tmp_path / "obj"
    _file(pkg / "__init__.py", TECH_SRC)
    _file(pkg / "defaults.yml", "{}\n")
    _file(pkg / f"{name}.tech.json", json.dumps({
        "name": name, "installs": [],
        "libraries": [{"lef_file": "cache/cells.lef", "spice_file": str(_file(pdk / "cells.spice"))}],
        "drc_decks": [{"tool_name": "pegasus", "deck_name": "drc", "path": str(_file(pdk / "drc" / "drc.rules"))}],
        "lvs_decks": [{"tool_name": "pegasus", "deck_name": "lvs", "path": str(_file(pdk / "lvs" / "lvs.rules"))}]}))
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    rtl = _file(tmp_path / "top.v", "module top; endmodule\n")
    cfg = {
        "vlsi.core.technology": name,
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.core.drc_tool": "hammer.drc.pegasus",
        "vlsi.core.lvs_tool": "hammer.lvs.pegasus",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        f"technology.{name}.drc_deck": str(pdk / "drc" / "drc.rules"),
        "vlsi.inputs.custom_sdc_files": [str(_file(pdk / "drc" / "extra.sdc"))],
        "vlsi.technology.extra_libraries": [{"library": {
            "lef_file": str(_file(obj / "macros" / "m.lef")), "spice_file": str(_file(obj / "macros" / "m.spice"))}}],
        "synthesis.inputs.top_module": "top",
        "synthesis.inputs.input_files": [str(rtl)],
        "par.inputs.input_files": [str(_file(obj / "syn-rundir" / "top.mapped.v"))],
        "drc.inputs.layout_file": str(_file(obj / "par-rundir" / "top.gds")),
        "lvs.inputs.layout_file": str(obj / "par-rundir" / "top.gds"),
        "lvs.inputs.schematic_files": [str(_file(obj / "par-rundir" / "top.lvs.v"))],
    }
    _file(tmp_path / "config.json", json.dumps(cfg))
    yield tmp_path
    sys.modules.pop(name, None)


def _driver(tmp_path: Path) -> HammerDriver:
    return HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=[str(tmp_path / "config.json")],
                                            log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))


def _keys(tmp_path: Path, action: str) -> dict:
    """What one action computes, in a fresh driver as each DAG task does."""
    return fp.stage_fingerprints(_driver(tmp_path), CLIDriver(), action, [])


def _global(keys: dict) -> dict:
    return {k: v for k, v in keys.items() if k.startswith("vlsi.")}


def test_every_action_computes_the_same_global_keys(design) -> None:
    out = {action: _keys(design, action) for action, _ in ACTIONS}
    assert set(_global(out["syn"])) == GLOBAL_KEYS
    assert all(_global(keys) == _global(out["syn"]) for keys in out.values())
    for action, tag in ACTIONS:
        own = {f"{tag}.{kind}_fingerprint_sha256" for kind in ("collateral", "upstream", "tool", "hooks")}
        assert set(out[action]) == GLOBAL_KEYS | own


def test_the_drc_hook_getter_runs_after_the_global_keys(design) -> None:
    driver = _driver(design)
    before = code_fingerprints.tech_fingerprint(driver.tech, fp.path_roots(driver))
    driver.tech.get_tech_drc_hooks("pegasus")
    assert code_fingerprints.tech_fingerprint(driver.tech, fp.path_roots(driver)) != before
    assert _global(_keys(design, "drc")) == _global(_keys(design, "syn"))


def test_decks_leave_the_global_key_in_every_action(design) -> None:
    syn, drc, lvs = (_keys(design, a) for a in ("syn", "drc", "lvs"))
    _file(design / "pdk" / "drc" / "drc.rules", "RULE changed\n")
    assert _keys(design, "syn") == syn
    assert _keys(design, "lvs") == lvs
    after = _keys(design, "drc")
    assert _global(after) == _global(drc)
    assert after["drc.collateral_fingerprint_sha256"] != drc["drc.collateral_fingerprint_sha256"]


def test_a_file_beside_a_deck_stays_global(design) -> None:
    syn, drc = _keys(design, "syn"), _keys(design, "drc")
    _file(design / "pdk" / "drc" / "extra.sdc", "set_load 2\n")
    assert _keys(design, "syn")["vlsi.collateral_fingerprint_sha256"] != syn["vlsi.collateral_fingerprint_sha256"]
    after = _keys(design, "drc")
    assert after["drc.collateral_fingerprint_sha256"] != drc["drc.collateral_fingerprint_sha256"]
    assert _global(after) == _global(_keys(design, "syn"))


def test_spice_and_upstream_files_reach_only_their_stage(design) -> None:
    out = {action: _keys(design, action) for action, _ in ACTIONS}
    _file(design / "pdk" / "cells.spice", ".subckt cell changed\n")
    _file(design / "obj" / "macros" / "m.spice", ".subckt m changed\n")
    _file(design / "obj" / "par-rundir" / "top.gds", "GDS changed\n")
    after = {action: _keys(design, action) for action, _ in ACTIONS}
    assert after["syn"] == out["syn"] and after["par"] == out["par"]
    assert after["drc"]["drc.upstream_fingerprint_sha256"] != out["drc"]["drc.upstream_fingerprint_sha256"]
    assert after["lvs"]["lvs.collateral_fingerprint_sha256"] != out["lvs"]["lvs.collateral_fingerprint_sha256"]
    assert after["lvs"]["lvs.upstream_fingerprint_sha256"] != out["lvs"]["lvs.upstream_fingerprint_sha256"]
    assert all(_global(after[a]) == _global(out["syn"]) for a in after)


def test_a_failure_names_the_key_and_path(design, monkeypatch) -> None:
    def broken(root=None):
        raise OSError(errno.EIO, "Input/output error", "/nfs/hammer/vlsi/hammer_tool.py")
    monkeypatch.setattr(code_fingerprints, "framework_fingerprint", broken)
    with pytest.raises(fp.FingerprintError) as e:
        _keys(design, "par")
    assert "vlsi.framework_fingerprint_sha256" in str(e.value)
    assert "/nfs/hammer/vlsi/hammer_tool.py" in str(e.value)


def test_only_the_four_checked_stages_have_fingerprints(design) -> None:
    assert set(fp.FINGERPRINT_STAGES.values()) == {"synthesis", "par", "drc", "lvs"}
    with pytest.raises(ValueError):
        fp.stage_fingerprints(_driver(design), CLIDriver(), "sim", [])
