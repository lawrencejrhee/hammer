import pytest

from hammer.vlsi import code_fingerprints as cf

DRIVER_SRC = '''import importlib

from hammer.vlsi import CLIDriver, HammerTool

for name in ["json"]:
    path = getattr(importlib.import_module(name), "__file__", None)
core = importlib.import_module("json")
helper = __import__("json")
{extra}

def par_note(x: HammerTool) -> bool:
    x.append("par note 1")
    return True


def syn_note(x: HammerTool) -> bool:
    x.append("syn note")
    return True


class LabDriver(CLIDriver):
    def get_extra_par_hooks(self):
        return [HammerTool.make_persistent_hook(par_note)]

    def get_extra_synthesis_hooks(self):
        return [HammerTool.make_persistent_hook(syn_note)]
'''


def _analysis(path):
    cf._SOURCES.pop(str(path), None)
    return cf._HookAnalysis([cf._HookModule(str(path), None, "__main__")], cf._base_names())


def _stage_code(path, stage):
    analysis = _analysis(path)
    return analysis.module_lines(analysis.kept(stage))


@pytest.fixture
def driver_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "_SOURCES", {})
    path = tmp_path / "lab-driver"

    def write(extra="", note="par note 1"):
        path.write_text(DRIVER_SRC.format(extra=extra).replace("par note 1", note))
        return path
    return write


def test_a_dynamic_import_at_import_time_keeps_par_code_out_of_synthesis(driver_file):
    path = driver_file()
    kept = {u.key for u in _analysis(path).kept("synthesis")}
    assert "par_note" not in kept and "syn_note" in kept
    before = _stage_code(path, "synthesis")
    driver_file(note="par note 2")
    assert _stage_code(path, "synthesis") == before


def test_a_dynamic_import_inside_a_hook_still_reaches_every_name(driver_file):
    path = driver_file(extra="\n\ndef loader(x: HammerTool) -> bool:\n"
                             "    return importlib.import_module(x.name).run(x)\n")
    analysis = _analysis(path)
    assert cf._EVERYTHING in next(u for u in analysis.units if u.key == "loader").refs


def test_globals_at_import_time_still_makes_every_unit_global(driver_file):
    path = driver_file(extra="globals()['late'] = 1\n")
    analysis = _analysis(path)
    assert analysis.global_reach == set(analysis.units)
