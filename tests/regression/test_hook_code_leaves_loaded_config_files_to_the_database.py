import importlib.machinery
import importlib.util
import sys
import types
import uuid

import pytest

from hammer.vlsi import code_fingerprints as cf

DRIVER_SRC = '''import os

from hammer.vlsi import CLIDriver, HammerTool

OBJ_DIR = os.environ["FP_TEST_OBJ_DIR"]
IO_YML = os.path.join(OBJ_DIR, "io-inputs.yml")
PIN_MAP = os.path.join(OBJ_DIR, "pins.yaml")
assert IO_YML and PIN_MAP


def par_note(x: HammerTool) -> bool:
    x.append("par note")
    return True


class LabDriver(CLIDriver):
    def get_extra_par_hooks(self):
        return [HammerTool.make_persistent_hook(par_note)]
'''


@pytest.fixture
def lab(tmp_path, monkeypatch):
    obj = tmp_path / "build"
    obj.mkdir()
    (obj / "io-inputs.yml").write_text('par.io_inputs_sha256: "1"\n')
    (obj / "pins.yaml").write_text("pin: 1\n")
    monkeypatch.setenv("FP_TEST_OBJ_DIR", str(obj))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    for attr in ("_SOURCES", "_LITERALS", "_FIRST"):
        monkeypatch.setattr(cf, attr, {})
    name = f"fplab_{uuid.uuid4().hex[:8]}"
    path = tmp_path / "launcher" / name
    path.parent.mkdir()
    path.write_text(DRIVER_SRC)
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_file_location(name, str(path), loader=loader))
    sys.modules[name] = mod
    loader.exec_module(mod)
    cli = mod.LabDriver.__new__(mod.LabDriver)

    def file_lines(stage, project_configs):
        options = types.SimpleNamespace(environment_configs=[], project_configs=project_configs)
        driver = types.SimpleNamespace(tech=None, obj_dir=str(obj), database=None, options=options)
        hooks = cli.get_extra_par_hooks() if stage == "par" else []
        return [line for line in cf.hooks_lines(driver, cli, stage, [], hooks) if line.startswith("file|")]

    yield obj, file_lines
    sys.modules.pop(name, None)


def test_a_config_file_named_by_driver_code_is_not_a_hooks_input(lab):
    obj, file_lines = lab
    assert file_lines("synthesis", [str(obj / "io-inputs.yml")]) == ["file|<OBJ_DIR>/pins.yaml:sha256="
                                                                     + cf.sha256_file(str(obj / "pins.yaml"))]
    assert not any("io-inputs.yml" in line for line in file_lines("par", [str(obj / "io-inputs.yml")]))


def test_the_same_file_counts_when_it_is_not_a_loaded_config(lab):
    _, file_lines = lab
    assert any("<OBJ_DIR>/io-inputs.yml" in line for line in file_lines("synthesis", []))
