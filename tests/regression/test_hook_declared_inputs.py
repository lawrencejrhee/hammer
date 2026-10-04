import functools
import re
from pathlib import Path

import pytest

import hammer.technology.sky130 as sky130
from hammer.lvs.pegasus import PegasusLVS
from hammer.vlsi import HammerToolHookAction, HammerToolStep, HookLocation
from hammer.vlsi import fingerprints as fp

_KEY = ("hammer.technology.sky130", "pegasus_lvs_add_130a_primitives")


class _Database:
    def __init__(self, settings):
        self.settings = settings

    def get(self, key):
        return self.settings[key]

    def get_setting(self, key, nullvalue=None):
        value = self.settings[key]
        return nullvalue if value is None else value

    def has_setting(self, key):
        return key in self.settings


class _Driver:
    def __init__(self, settings):
        self.database = _Database(settings)


class _LVS(PegasusLVS):
    def __init__(self, settings, ctl):
        self._settings = settings
        self._ctl = ctl

    def get_setting(self, key, nullvalue=None):
        value = self._settings.get(key)
        return nullvalue if value is None else value

    @property
    def lvs_ctl_file(self):
        return self._ctl


def _settings(tmp_path, library, misc):
    return {"technology.sky130.sky130A": str(tmp_path / "sky130A"),
            "technology.sky130.misc_tapeout_collateral": str(misc) if misc else None,
            "technology.sky130.stdcell_library": library,
            "technology.sky130.lvs_blackbox_srams": False}


def _lvs_hooks(settings):
    tech = sky130.SKY130Tech()
    tech.use_sram22 = False
    tech.set_database(_Database(settings))
    return tech.get_tech_lvs_hooks("pegasus")


def _hook_paths(tmp_path, settings):
    """Run the real hook on an empty control file and read back the schematic_path lines it appends."""
    ctl = tmp_path / "pegasuslvsctl"
    ctl.write_text("// control file\n")
    assert sky130.pegasus_lvs_add_130a_primitives(_LVS(settings, str(ctl)))
    return re.findall(r'schematic_path "([^"]*)" spice;', ctl.read_text())


@pytest.mark.parametrize("misc", [None, "misc"])
def test_registry_lists_exactly_what_the_real_sky130_hook_adds(tmp_path, misc):
    settings = _settings(tmp_path, "sky130_scl", tmp_path / misc if misc else None)
    expected = _hook_paths(tmp_path, settings)
    assert expected and any(p.startswith(str(tmp_path / "misc")) for p in expected) == bool(misc)
    func = sky130.pegasus_lvs_add_130a_primitives
    assert fp._HOOK_INPUTS[_KEY](func, settings.get) == expected


def test_scl_lvs_primitives_go_to_the_stage_whose_hooks_hold_the_step(tmp_path):
    misc = tmp_path / "misc"
    settings = _settings(tmp_path, "sky130_scl", misc)
    paths = _hook_paths(tmp_path, settings)
    created = [Path(paths[0]), next(Path(p) for p in paths if p.startswith(str(misc)))]
    for p in created:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("* model\n")
    roots = fp.make_roots(None, str(tmp_path / "obj"), hammer_root="")
    out = fp.hook_input_lines(_Driver(settings), {"lvs": _lvs_hooks(settings)}, roots)
    assert out == {"lvs": sorted(fp.file_line(p, roots) for p in paths)}
    assert sum(not line.startswith("MISSING:") for line in out["lvs"]) == 2
    created[1].write_text("* patched model\n")
    assert fp.hook_input_lines(_Driver(settings), {"lvs": _lvs_hooks(settings)}, roots) != out


def test_hd_does_not_register_the_step(tmp_path):
    settings = _settings(tmp_path, "sky130_fd_sc_hd", tmp_path / "misc")
    hooks = _lvs_hooks(settings)
    assert all(h.step.func.__name__ != _KEY[1] for h in hooks if h.step is not None)
    assert fp.hook_input_lines(_Driver(settings), {"lvs": hooks}, ()) == {"lvs": []}


def test_scope_follows_the_hook_list_and_steps_may_be_wrapped(tmp_path):
    settings = _settings(tmp_path, "sky130_scl", None)
    step = HammerToolStep(functools.partial(sky130.pegasus_lvs_add_130a_primitives), "add_primitives")
    action = HammerToolHookAction(HookLocation.InsertPostStep, "generate_lvs_ctl_file", step)
    out = fp.hook_input_lines(_Driver(settings), {"syn": [action], "drc": [step], "par": []}, ())
    expected = sorted(fp.file_line(p) for p in _hook_paths(tmp_path, settings))
    assert out == {"synthesis": expected, "drc": expected, "par": []}
