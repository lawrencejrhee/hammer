import functools
import importlib
import importlib.machinery
import importlib.util
import sys
import types
import uuid

import pytest

from hammer.vlsi import code_fingerprints as cf
from hammer.vlsi.hooks import HammerToolHookAction, HammerToolStep, HookLocation


STAGES = (("synthesis", "syn", "synthesis"), ("par", "par", "par"), ("drc", "drc", "drc"), ("lvs", "lvs", "lvs"))

TECH_SRC = '''from hammer.tech import HammerTechnology
from hammer.vlsi import HammerTool


def shared_tcl(ht: HammerTool) -> bool:
    ht.append("set shared 1")
    return True


def syn_only(ht: HammerTool) -> bool:
    return shared_tcl(ht)


def par_only(ht: HammerTool) -> bool:
    ht.append("par knob 1")
    return shared_tcl(ht)


def drc_only(ht: HammerTool) -> bool:
    ht.append("drc rule 1")
    return True


class FakeTech(HammerTechnology):
    def get_tech_syn_hooks(self, tool_name):
        return [HammerTool.make_post_insertion_hook("init_environment", syn_only)]

    def get_tech_par_hooks(self, tool_name):
        return [HammerTool.make_post_insertion_hook("floorplan_design", par_only)]

    def get_tech_drc_hooks(self, tool_name):
        return [HammerTool.make_pre_insertion_hook("run_drc", drc_only)]
'''

STATE_TECH_SRC = '''from hammer.tech import HammerTechnology
from hammer.vlsi import HammerTool

EXTRA_CELLS = []


def syn_cells(ht: HammerTool) -> bool:
    ht.append(" ".join(EXTRA_CELLS))
    return True


def par_mode(ht: HammerTool) -> bool:
    ht.append(ht.technology.deck_mode)
    return True


def drc_only(ht: HammerTool) -> bool:
    ht.append("drc rule 1")
    return True


class FakeTech(HammerTechnology):
    def get_tech_syn_hooks(self, tool_name):
        return [HammerTool.make_post_insertion_hook("init_environment", syn_cells)]

    def get_tech_par_hooks(self, tool_name):
        return [HammerTool.make_post_insertion_hook("floorplan_design", par_mode)]

    def get_tech_drc_hooks(self, tool_name):
        self.deck_mode = "fast"
        EXTRA_CELLS.append("io_cell")
        return [HammerTool.make_pre_insertion_hook("run_drc", drc_only)]
'''

DRIVER_SRC = '''import os

from hammer.vlsi import CLIDriver, HammerTool

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_MAP = os.path.join(HERE, "pins.yaml")


def place_pins(x: HammerTool) -> bool:
    with open(PIN_MAP) as f:
        x.append(f.read())
    return True


def lvs_fix(x: HammerTool) -> bool:
    x.append("lvs fix")
    return True


class Driver(CLIDriver):
    def get_extra_par_hooks(self):
        return [HammerTool.make_post_insertion_hook("place_opt_design", place_pins)]

    def get_extra_lvs_hooks(self):
        return [HammerTool.make_pre_insertion_hook("run_lvs", lvs_fix)]


if __name__ == "__main__":
    Driver().main()
'''

HELPER_SRC = '''def helper_step(ht, value):
    ht.append("helper " + str(value))
    return True


class Stepper:
    def __call__(self, ht):
        return True

    def method(self, ht):
        return True
'''


def _load(name, path):
    sys.modules.pop(name, None)
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, str(path), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _fresh_process(monkeypatch):
    for attr in ("_SOURCES", "_LITERALS", "_FIRST"):
        monkeypatch.setattr(cf, attr, {})


class Flow:
    def __init__(self, tmp_path, monkeypatch, tech_src=TECH_SRC, drv_src=DRIVER_SRC, tech_dir="tech", lib=None):
        tag = uuid.uuid4().hex[:8]
        self.mp = monkeypatch
        self.root = tmp_path
        self.tech_name = f"fptech_{tag}"
        self.drv_name = f"fpdrv_{tag}"
        self.pkg = f"fppkg_{tag}"
        self.libdir = tmp_path / tech_dir
        self.tech_path = self.libdir / f"{self.tech_name}.py"
        self.drv_path = tmp_path / "drv" / "hammer-driver"
        self.pins = tmp_path / "drv" / "pins.yaml"
        files = {self.tech_path: tech_src, self.drv_path: drv_src, self.pins: "pin: 1\n"}
        files.update({self.libdir / self.sub(rel): text for rel, text in (lib or {}).items()})
        for path, text in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.sub(text))
        monkeypatch.syspath_prepend(str(self.libdir))

    def sub(self, text):
        return text.replace("@PKG@", self.pkg).replace("@ROOT@", str(self.root))

    def edit(self, path, old, new):
        text = path.read_text()
        assert text.count(old) == 1, old
        path.write_text(text.replace(old, new))

    def forget(self):
        for name in [n for n in sys.modules if n.split(".")[0] in (self.pkg, self.tech_name, self.drv_name)]:
            sys.modules.pop(name, None)

    def run(self):
        _fresh_process(self.mp)
        self.forget()
        importlib.invalidate_caches()
        tech_mod = _load(self.tech_name, self.tech_path)
        drv_mod = _load(self.drv_name, self.drv_path)
        tech = tech_mod.FakeTech()
        cli = drv_mod.Driver.__new__(drv_mod.Driver)
        driver = types.SimpleNamespace(tech=tech, obj_dir=str(self.root), database=None)
        hooks = {stage: (getattr(tech, f"get_tech_{t}_hooks")("faketool"), getattr(cli, f"get_extra_{u}_hooks")())
                 for stage, t, u in STAGES}
        return driver, cli, hooks

    def keys(self):
        driver, cli, hooks = self.run()
        return {stage: cf.hooks_fingerprint(driver, cli, stage, *hooks[stage]) for stage, _, _ in STAGES}

    def lines(self, stage):
        driver, cli, hooks = self.run()
        return cf.hooks_lines(driver, cli, stage, *hooks[stage])


def _changed(before, after):
    return {s for s in before if before[s] != after[s]}


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    made = []

    def make(tech_src=TECH_SRC, **kwargs):
        f = Flow(tmp_path, monkeypatch, tech_src, **kwargs)
        made.append(f)
        return f

    yield make
    for f in made:
        f.forget()


def test_keys_are_stable_across_processes(flow):
    f = flow()
    assert f.keys() == f.keys()


def test_drc_only_hook_edit_reruns_drc_only(flow):
    f = flow()
    base = f.keys()
    f.edit(f.tech_path, '"drc rule 1"', '"drc rule 2"')
    assert _changed(base, f.keys()) == {"drc"}


def test_shared_helper_edit_reruns_every_stage_that_reaches_it(flow):
    f = flow()
    base = f.keys()
    f.edit(f.tech_path, '"set shared 1"', '"set shared 2"')
    assert _changed(base, f.keys()) == {"synthesis", "par"}


def test_driver_hook_edit_reruns_its_stage_only(flow):
    f = flow()
    base = f.keys()
    f.edit(f.drv_path, '"lvs fix"', '"lvs fix 2"')
    assert _changed(base, f.keys()) == {"lvs"}


def test_comment_and_docstring_edits_change_nothing(flow):
    f = flow()
    base = f.keys()
    head = "def drc_only(ht: HammerTool) -> bool:\n"
    f.edit(f.tech_path, head, head + '    """Blackbox IO cells."""\n    # tuned\n')
    f.edit(f.drv_path, "import os\n", '"""Chip driver."""\n# header\nimport os  # stdlib\n')
    assert _changed(base, f.keys()) == set()


def test_adding_or_removing_a_hook_action_changes_that_stage(flow):
    f = flow()
    base = f.keys()
    old = 'return [HammerTool.make_post_insertion_hook("floorplan_design", par_only)]'
    new = ('return [HammerTool.make_post_insertion_hook("floorplan_design", par_only),\n'
           '                HammerTool.make_removal_hook("place_tap_cells")]')
    f.edit(f.tech_path, old, new)
    added = f.keys()
    assert _changed(base, added) == {"par"}
    f.edit(f.tech_path, new, old)
    assert f.keys() == base


def test_literal_file_named_by_par_hook_reruns_par(flow):
    f = flow()
    base = f.keys()
    par_files = [line for line in f.lines("par") if line.startswith("file|")]
    assert any("drv/pins.yaml" in line for line in par_files)
    assert not any(line.startswith("file|") for line in f.lines("synthesis"))
    f.pins.write_text("pin: 2\n")
    assert _changed(base, f.keys()) == {"par"}


def test_extensionless_driver_script_is_parsed(flow):
    f = flow()
    lines = f.lines("par")
    assert any(line.startswith("code|hammer-driver=") and "raw:" not in line for line in lines)
    assert any(line.startswith(f"code|{f.tech_name}.py=") for line in lines)


def test_syn_and_synthesis_give_the_same_hooks_key(flow):
    f = flow()
    driver, cli, hooks = f.run()
    key = cf.hooks_fingerprint(driver, cli, "synthesis", *hooks["synthesis"])
    assert cf.hooks_fingerprint(driver, cli, "syn", *hooks["synthesis"]) == key


def test_getter_side_effects_reach_the_stages_that_read_them(flow):
    f = flow(STATE_TECH_SRC)
    base = f.keys()
    f.edit(f.tech_path, 'self.deck_mode = "fast"', 'self.deck_mode = "slow"')
    assert _changed(base, f.keys()) == {"drc", "par", "synthesis"}


def test_mutated_module_list_reaches_its_reader(flow):
    f = flow(STATE_TECH_SRC)
    base = f.keys()
    f.edit(f.tech_path, 'EXTRA_CELLS.append("io_cell")', 'EXTRA_CELLS.append("io_cell_2")')
    assert _changed(base, f.keys()) == {"drc", "par", "synthesis"}


def _action(func, name="step"):
    return HammerToolHookAction(location=HookLocation.InsertPreStep, target_name="run",
                                step=HammerToolStep(func=func, name=name))


def test_partial_builtin_and_callable_steps_do_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    name = f"fphelper_{uuid.uuid4().hex[:8]}"
    path = tmp_path / "helpers" / f"{name}.py"
    path.parent.mkdir()
    path.write_text(HELPER_SRC)

    def key():
        _fresh_process(monkeypatch)
        mod = _load(name, path)
        stepper = mod.Stepper()
        hooks = [_action(functools.partial(mod.helper_step, value=object())),
                 _action(functools.partial(functools.partial(mod.helper_step), object())),
                 _action(len), _action(print), _action(stepper), _action(stepper.method),
                 _action(lambda ht: True)]
        return cf.hooks_fingerprint(None, None, "par", hooks, []), cf.hooks_lines(None, None, "par", hooks, [])

    try:
        base, lines = key()
        assert any(line.endswith("builtins:len") for line in lines)
        assert any(f"{name}:helper_step=" in line for line in lines)
        assert key()[0] == base
        path.write_text(HELPER_SRC.replace('"helper "', '"helper2 "'))
        assert key()[0] != base
    finally:
        sys.modules.pop(name, None)


def test_framework_and_hammer_steps_are_identity_only(flow):
    f = flow()
    driver, cli, _ = f.run()
    from hammer.vlsi import HammerTool
    removal = HammerTool.make_removal_hook("place_tap_cells")
    lines = cf.hooks_lines(driver, cli, "par", [removal], [])
    steps = [line for line in lines if line.startswith("step|")]
    assert steps and all("=" not in line for line in steps)


def test_unparseable_module_is_hashed_raw(tmp_path, monkeypatch):
    _fresh_process(monkeypatch)
    path = tmp_path / "broken.py"
    path.write_bytes(b"def f(:\n    pass\n")
    mod = cf._HookModule(str(path), None)
    assert mod.tree is None and mod.raw.startswith("raw:")
    path.write_bytes(b"x = 1\0\n")
    _fresh_process(monkeypatch)
    assert cf._HookModule(str(path), None).raw.startswith("raw:")


IMPORT_DRV_SRC = '''from hammer.vlsi import CLIDriver, HammerTool


def par_lib(x: HammerTool) -> bool:
    from @PKG@ import mylib
    return mylib.f(x)


def lvs_lib(x: HammerTool) -> bool:
    import @PKG@.other
    return @PKG@.other.h(x)


class Driver(CLIDriver):
    def get_extra_par_hooks(self):
        return [HammerTool.make_post_insertion_hook("place_opt_design", par_lib)]

    def get_extra_lvs_hooks(self):
        return [HammerTool.make_pre_insertion_hook("run_lvs", lvs_lib)]
'''

IMPORT_LIB = {
    "@PKG@/__init__.py": "",
    "@PKG@/mylib.py": "from .mylib2 import g\n\n\ndef f(x):\n    return g(x)\n",
    "@PKG@/mylib2.py": 'def g(x):\n    x.append("v1")\n    return True\n',
    "@PKG@/other.py": 'def h(x):\n    x.append("o1")\n    return True\n',
}


def test_helper_modules_imported_inside_hook_bodies_are_followed(flow):
    f = flow(drv_src=IMPORT_DRV_SRC, lib=IMPORT_LIB)
    base = f.keys()
    assert f.pkg not in sys.modules
    pkg = f.libdir / f.pkg
    f.edit(pkg / "mylib2.py", '"v1"', '"v2"')
    second = f.keys()
    assert _changed(base, second) == {"par"}
    f.edit(pkg / "mylib.py", "return g(x)", "return g(x) and True")
    third = f.keys()
    assert _changed(second, third) == {"par"}
    f.edit(pkg / "other.py", '"o1"', '"o2"')
    assert _changed(third, f.keys()) == {"lvs"}


ALIAS_DRV_SRC = '''from hammer.vlsi import CLIDriver, HammerTool
from @PKG@.shared import shared as sh


def par_alias(x: HammerTool) -> bool:
    return sh(x)


def lvs_alias(x: HammerTool) -> bool:
    from @PKG@.shared import shared as sh2
    return sh2(x)


def drc_named(x: HammerTool) -> bool:
    from @PKG@.shared import shared
    return shared(x)


class Driver(CLIDriver):
    def get_extra_par_hooks(self):
        return [HammerTool.make_post_insertion_hook("place_opt_design", par_alias)]

    def get_extra_drc_hooks(self):
        return [HammerTool.make_pre_insertion_hook("run_drc", drc_named)]

    def get_extra_lvs_hooks(self):
        return [HammerTool.make_pre_insertion_hook("run_lvs", lvs_alias)]
'''

ALIAS_LIB = {
    "@PKG@/__init__.py": "",
    "@PKG@/shared.py": 'def shared(x):\n    x.append("s1")\n    return True\n',
}


def test_helper_reached_through_an_import_alias_counts_for_that_stage(flow):
    f = flow(drv_src=ALIAS_DRV_SRC, lib=ALIAS_LIB)
    base = f.keys()
    f.edit(f.libdir / f.pkg / "shared.py", '"s1"', '"s2"')
    assert _changed(base, f.keys()) == {"par", "drc", "lvs"}


LITERAL_DRV_SRC = '''import os
from pathlib import Path

from hammer.vlsi import CLIDriver, HammerTool

TCL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tcl")
PINS = Path(__file__).resolve().parent / "pins2.yaml"


def par_sources(x: HammerTool) -> bool:
    x.append("source @ROOT@/fix.tcl")
    x.append(f"source {TCL_DIR}/fix2.tcl")
    x.append(str(PINS))
    x.append(" ".join(["@ROOT@/por.gds"]))
    return True


class Driver(CLIDriver):
    def get_extra_par_hooks(self):
        return [HammerTool.make_post_insertion_hook("place_opt_design", par_sources)]
'''


def test_files_named_by_par_hook_literals_rerun_par_only(flow, tmp_path):
    f = flow(drv_src=LITERAL_DRV_SRC)
    files = {tmp_path / "fix.tcl": "set a 1\n", tmp_path / "drv" / "tcl" / "fix2.tcl": "set b 1\n",
             tmp_path / "drv" / "pins2.yaml": "pin: 1\n", tmp_path / "por.gds": "gds 1\n"}
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    keys = f.keys()
    for path, text in files.items():
        path.write_text(text.replace("1", "2"))
        after = f.keys()
        assert _changed(keys, after) == {"par"}, path
        keys = after


SCOPE_TECH_SRC = '''from hammer.tech import HammerTechnology
from hammer.vlsi import HammerTool


def register(func):
    return func


@register
def lvs_decorated(ht: HammerTool) -> bool:
    ht.append("lvs deco 1")
    return True


def make_step(text):
    def step(ht: HammerTool) -> bool:
        ht.append(text)
        return True
    return step


def par_only(ht: HammerTool) -> bool:
    ht.append("par knob 1")
    return True


def drc_only(ht: HammerTool) -> bool:
    ht.append("drc rule 1")
    return True


class FakeTech(HammerTechnology):
    def get_tech_syn_hooks(self, tool_name):
        mode = "fast"

        def nested(ht: HammerTool) -> bool:
            ht.append("nested " + mode)
            return True
        return [HammerTool.make_post_insertion_hook("init_environment", nested)]

    def get_tech_par_hooks(self, tool_name):
        return [HammerTool.make_post_insertion_hook("floorplan_design", make_step("par text 1")),
                HammerTool.make_post_insertion_hook("place_opt_design", par_only)]

    def get_tech_drc_hooks(self, tool_name):
        return [HammerTool.make_pre_insertion_hook("run_drc", drc_only)]

    def get_tech_lvs_hooks(self, tool_name):
        return [HammerTool.make_pre_insertion_hook("run_lvs", lvs_decorated)]
'''

ALL_STAGES = {"synthesis", "par", "drc", "lvs"}


@pytest.mark.parametrize("old,new,expected", [
    ('mode = "fast"', 'mode = "slow"', {"synthesis"}),
    ('"nested "', '"nested hook "', {"synthesis"}),
    ('"par text 1"', '"par text 2"', {"par"}),
    ('"drc rule 1"', '"drc rule 2"', {"drc"}),
    ('"lvs deco 1"', '"lvs deco 2"', ALL_STAGES),
])
def test_nested_hooks_factories_and_decorators_are_scoped(flow, old, new, expected):
    f = flow(SCOPE_TECH_SRC)
    base = f.keys()
    f.edit(f.tech_path, old, new)
    assert _changed(base, f.keys()) == expected


def test_module_level_globals_use_makes_every_edit_global(flow):
    f = flow(SCOPE_TECH_SRC + '\nglobals().setdefault("EXTRA", 1)\n')
    base = f.keys()
    f.edit(f.tech_path, '"drc rule 1"', '"drc rule 2"')
    assert _changed(base, f.keys()) == ALL_STAGES


def test_hook_using_globals_sees_every_other_stage(flow):
    f = flow(SCOPE_TECH_SRC.replace('ht.append("drc rule 1")', 'ht.append(str(len(globals())))'))
    base = f.keys()
    f.edit(f.tech_path, '"par knob 1"', '"par knob 2"')
    assert _changed(base, f.keys()) == {"par", "drc"}


STORE_DRV_SRC = '''from hammer.vlsi import CLIDriver, HammerTool

KNOBS = {"opts": {}}
MODE = {}


def register(table, key, value):
    table[key] = value


def par_reads(x: HammerTool) -> bool:
    x.append(str(KNOBS) + str(MODE) + getattr(x, "driver_mode", ""))
    return True


def step(x: HammerTool) -> bool:
    return True


class Driver(CLIDriver):
    def get_extra_par_hooks(self):
        return [HammerTool.make_post_insertion_hook("place_opt_design", par_reads)]

    def get_extra_drc_hooks(self):
        KNOBS["opts"]["drc"] = "nested 1"
        return [HammerTool.make_pre_insertion_hook("run_drc", step)]

    def get_extra_lvs_hooks(self):
        register(MODE, "lvs", "passed 1")
        return [HammerTool.make_pre_insertion_hook("run_lvs", step)]

    def get_extra_syn_hooks(self):
        setattr(HammerTool, "driver_mode", "set 1")
        return []
'''


@pytest.mark.parametrize("old,new,expected", [
    ('"nested 1"', '"nested 2"', {"drc", "par"}),
    ('"passed 1"', '"passed 2"', {"lvs", "par"}),
    ('"set 1"', '"set 2"', {"synthesis", "par"}),
])
def test_getter_writes_through_chains_calls_and_setattr_reach_readers(flow, old, new, expected):
    f = flow(drv_src=STORE_DRV_SRC)
    base = f.keys()
    f.edit(f.drv_path, old, new)
    assert _changed(base, f.keys()) == expected


SITE_TECH_SRC = TECH_SRC.replace("from hammer.vlsi import HammerTool\n",
                                 "from hammer.vlsi import HammerTool\nfrom @PKG@ import site_drc\n").replace(
    '"run_drc", drc_only)', '"run_drc", site_drc)')


def test_site_packages_tech_and_step_code_is_hashed(flow):
    lib = {"@PKG@.py": ('from hammer.vlsi import HammerTool\n\n\n'
                        'def site_drc(ht: HammerTool) -> bool:\n    ht.append("site 1")\n    return True\n')}
    f = flow(SITE_TECH_SRC, tech_dir="site-packages", lib=lib)
    base = f.keys()
    assert any(line.startswith(f"code|{f.tech_name}.py=") for line in f.lines("drc"))
    f.edit(f.tech_path, '"par knob 1"', '"par knob 2"')
    second = f.keys()
    assert _changed(base, second) == {"par"}
    f.edit(f.libdir / f"{f.pkg}.py", '"site 1"', '"site 2"')
    assert _changed(second, f.keys()) == {"drc"}


def test_partial_arguments_are_canonical(tmp_path):
    def step(ht, cells, where, opts):
        return True

    driver = types.SimpleNamespace(tech=None, obj_dir=str(tmp_path), database=None)
    lines = []
    for cells in ({"c", "a", "b"}, frozenset(["b", "c", "a"])):
        part = functools.partial(step, cells=cells, where=tmp_path / "x.tcl", opts={"z": {"y", "x"}, "a": 1})
        lines.append([line for line in cf.hooks_lines(driver, None, "par", [_action(part)], [])
                      if line.startswith("step|")][0])
    assert "'a', 'b', 'c'" in lines[0] and "'x', 'y'" in lines[0]
    assert "<OBJ_DIR>/x.tcl" in lines[0] and str(tmp_path) not in lines[0]
    assert lines[0].replace("frozenset", "set") == lines[1].replace("frozenset", "set")
