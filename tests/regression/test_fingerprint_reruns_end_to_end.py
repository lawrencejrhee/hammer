import json
import os
import sys
import uuid
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, HammerTool
from hammer.vlsi import code_fingerprints, pd_cache, pd_store, rtl_check

TECH_SRC = '''import os

from hammer.tech import HammerTechnology


class ChurnTech(HammerTechnology):
    def post_install_script(self):
        with open(os.environ["CACHE_LEF_SRC"]) as f:
            data = f.read()
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(os.path.join(self.cache_dir, "cells.lef"), "w") as f:
            f.write(data)


tech = ChurnTech()
'''

PAR_HOOKS = []


def _par_note(x: HammerTool) -> bool:
    return True


class HookDriver(CLIDriver):
    def get_extra_par_hooks(self):
        return list(PAR_HOOKS)


def _fixed_digest(paths, include_dirs=(), defines=(), top_module=None):
    return "0" * 64, []


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _fixed_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _fixed_digest)
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN", raising=False)
    monkeypatch.delenv("HAMMER_SUBSTEP_RESUME", raising=False)
    monkeypatch.delenv("HAMMER_PD_COLLAT_DEBUG", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    PAR_HOOKS.clear()


class Flow:
    def __init__(self, tmp_path: Path, monkeypatch, capsys) -> None:
        self.tmp = tmp_path
        self.capsys = capsys
        self.tech = f"churn_{uuid.uuid4().hex[:8]}"
        pkg = tmp_path / "site" / self.tech
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(TECH_SRC)
        (pkg / f"{self.tech}.tech.json").write_text(json.dumps(
            {"name": self.tech, "installs": [], "libraries": [{"lef_file": "cache/cells.lef"}]}))
        (pkg / "defaults.yml").write_text("{}\n")
        monkeypatch.syspath_prepend(str(tmp_path / "site"))
        self.lef_src = tmp_path / "pdk_src" / "cells.lef"
        self.lef_src.parent.mkdir()
        self.lef_src.write_text("MACRO cell_a\nEND cell_a\n")
        monkeypatch.setenv("CACHE_LEF_SRC", str(self.lef_src))
        rtl = tmp_path / "top.v"
        rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
        (tmp_path / "mock").mkdir()
        (tmp_path / "config.json").write_text(json.dumps({
            "vlsi.core.technology": self.tech,
            "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
            "vlsi.core.par_tool": "hammer.par.nop",
            "vlsi.inputs.hierarchical.config_source": "none",
            "vlsi.technology.extra_macro_sizes": [],
            "synthesis.inputs.top_module": "dummy",
            "synthesis.inputs.input_files": [str(rtl)],
            "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
        }, cls=HammerJSONEncoder, indent=4))

    def obj(self, name: str = "obj") -> Path:
        return self.tmp / name

    def cache_lef(self, name: str = "obj") -> Path:
        return self.obj(name) / f"tech-{self.tech}-cache" / "cells.lef"

    def run(self, action: str, *configs: str, obj: str = "obj", driver: type = CLIDriver) -> str:
        self.capsys.readouterr()
        args = [action]
        for c in (str(self.tmp / "config.json"),) + configs:
            args += ["-p", c]
        with pytest.raises(SystemExit) as cm:
            driver().main(args=args + ["-o", str(self.tmp / f"{action}-{obj}-out.json"),
                                       "--obj_dir", str(self.obj(obj)), "--log", str(self.tmp / "log.txt")])
        out = self.capsys.readouterr().out
        assert cm.value.code == 0, out
        return out

    def syn(self, obj: str = "obj", driver: type = CLIDriver, *configs: str) -> str:
        return self.run("syn", *configs, obj=obj, driver=driver)

    def par(self, obj: str = "obj", driver: type = CLIDriver) -> str:
        o = self.obj(obj)
        self.run("syn-to-par", str(o / "syn-rundir" / "syn-output-full.json"), obj=obj, driver=driver)
        return self.run("par", str(self.tmp / f"syn-to-par-{obj}-out.json"), obj=obj, driver=driver)


@pytest.fixture
def flow(tmp_path, monkeypatch, capsys):
    f = Flow(tmp_path, monkeypatch, capsys)
    yield f
    sys.modules.pop(f.tech, None)


def _backdate(path: Path) -> int:
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    return path.stat().st_mtime_ns


def test_second_syn_skips_although_the_tech_cache_is_rewritten(flow) -> None:
    assert "can skip syn" not in flow.syn()
    before = _backdate(flow.cache_lef())
    assert "can skip syn" in flow.syn()
    assert flow.cache_lef().stat().st_mtime_ns != before


def test_tech_cache_content_change_reruns_syn(flow) -> None:
    flow.syn()
    flow.lef_src.write_text("MACRO cell_a\nEND cell_a\nMACRO cell_b\nEND cell_b\n")
    out = flow.syn()
    assert "can skip syn" not in out
    assert "vlsi.collateral_fingerprint_sha256" in out
    assert "can skip syn" in flow.syn()


def test_same_design_in_two_build_dirs_has_one_cache_key(flow, monkeypatch) -> None:
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)
    loads, stores = [], []
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: loads.append(key))
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda stage, key, *a, **k: stores.append((stage, key)))
    for name, sdc in (("objA", "set_load 1\n"), ("objB", "set_load 1\n"), ("objC", "set_load 2\n")):
        (flow.obj(name) / "constraints").mkdir(parents=True)
        (flow.obj(name) / "constraints" / "io.sdc").write_text(sdc)
        extra = flow.tmp / f"{name}.json"
        extra.write_text(json.dumps({"vlsi.inputs.custom_sdc_files": [str(flow.obj(name) / "constraints" / "io.sdc")]}))
        flow.syn(name, CLIDriver, str(extra))
        flow.par(obj=name)
    assert [s for s, _ in stores] == ["synthesis", "par"] * 3
    assert stores[0] == stores[2] and stores[1] == stores[3]
    assert stores[4] != stores[0] and stores[5] != stores[1]
    assert loads == [k for _, k in stores]


def _changed_for(real, match):
    def wrapper(*args, **kwargs):
        value = real(*args, **kwargs)
        return "edited-" + value if match(*args, **kwargs) else value
    return wrapper


def test_framework_change_reruns_every_stage(flow, monkeypatch) -> None:
    flow.syn()
    flow.par()
    monkeypatch.setattr(code_fingerprints, "framework_fingerprint",
                        _changed_for(code_fingerprints.framework_fingerprint, lambda *a, **k: True))
    out = flow.syn()
    assert "can skip syn" not in out and "vlsi.framework_fingerprint_sha256" in out
    assert "can skip par" not in flow.par()
    assert "can skip syn" in flow.syn()


def test_par_tool_change_reruns_par_but_not_syn(flow, monkeypatch) -> None:
    flow.syn()
    flow.par()
    monkeypatch.setattr(code_fingerprints, "tool_fingerprint",
                        _changed_for(code_fingerprints.tool_fingerprint, lambda mod: mod == "hammer.par.nop"))
    assert "can skip syn" in flow.syn()
    out = flow.par()
    assert "can skip par" not in out and "par.tool_fingerprint_sha256" in out
    assert "can skip par" in flow.par()


def test_synthesis_tool_change_reruns_syn_and_then_par(flow, monkeypatch) -> None:
    flow.syn()
    flow.par()
    monkeypatch.setattr(code_fingerprints, "tool_fingerprint",
                        _changed_for(code_fingerprints.tool_fingerprint,
                                     lambda mod: mod == "hammer.synthesis.mocksynth"))
    out = flow.syn()
    assert "can skip syn" not in out and "synthesis.tool_fingerprint_sha256" in out
    out = flow.par()
    assert "can skip par" not in out and "NEEDS TO RERUN WAS TRUE" in out


def test_par_only_hook_change_does_not_rerun_syn(flow) -> None:
    flow.syn(driver=HookDriver)
    flow.par(driver=HookDriver)
    PAR_HOOKS.append(HammerTool.make_persistent_hook(_par_note))
    assert "can skip syn" in flow.syn(driver=HookDriver)
    out = flow.par(driver=HookDriver)
    assert "can skip par" not in out and "par.hooks_fingerprint_sha256" in out
    assert "can skip par" in flow.par(driver=HookDriver)


def _no_slang(paths, include_dirs=(), defines=(), top_module=None):
    raise rtl_check.SlangNotFound("not installed")


@pytest.mark.parametrize("fallback", [True, False])
def test_include_dir_headers_count_only_under_the_byte_fallback(flow, monkeypatch, fallback) -> None:
    if fallback:
        monkeypatch.setattr(rtl_check, "digest_units", _no_slang)
    header = flow.tmp / "inc" / "defs.vh"
    header.parent.mkdir()
    header.write_text("`define WIDTH 8\n")
    extra = flow.tmp / "inc.json"
    extra.write_text(json.dumps({"synthesis.inputs.include_dirs": [str(header.parent)]}))
    flow.syn("obj", CLIDriver, str(extra))
    assert "can skip syn" in flow.syn("obj", CLIDriver, str(extra))
    header.write_text("`define WIDTH 16\n")
    out = flow.syn("obj", CLIDriver, str(extra))
    assert ("can skip syn" not in out) == fallback
