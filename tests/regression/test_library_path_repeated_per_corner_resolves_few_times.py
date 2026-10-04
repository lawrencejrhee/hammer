import collections
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

from hammer.vlsi import CLIDriver, HammerDriver, HammerDriverOptions
from hammer.vlsi import fingerprints as fp

TECH_SRC = '''import os

from hammer.tech import HammerTechnology


class ManyLibsTech(HammerTechnology):
    def post_install_script(self):
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(os.path.join(self.cache_dir, "cells.lef"), "w") as f:
            f.write("MACRO cell\\nEND cell\\n")


tech = ManyLibsTech()
'''

CORNERS = 300


@pytest.fixture(autouse=True)
def _no_debug_output(monkeypatch):
    monkeypatch.delenv("HAMMER_PD_COLLAT_DEBUG", raising=False)


def _file(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def design(tmp_path, monkeypatch):
    name = f"manylibs_{uuid.uuid4().hex[:8]}"
    pkg = tmp_path / "site" / name
    pdk = tmp_path / "pdk"
    _file(pkg / "__init__.py", TECH_SRC)
    _file(pkg / "defaults.yml", "{}\n")
    lib = _file(pdk / "lib" / "cells__tt.lib", "library(cells) {}\n")
    gds = _file(pdk / "gds" / "cells.gds", "GDS\n")
    _file(pkg / f"{name}.tech.json", json.dumps({
        "name": name, "installs": [],
        "libraries": [{"nldm_liberty_file": str(lib), "gds_file": str(gds), "lef_file": "cache/cells.lef"}
                      for _ in range(CORNERS)]}))
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    cfg = {
        "vlsi.core.technology": name,
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
    }
    _file(tmp_path / "config.json", json.dumps(cfg))
    yield tmp_path, lib
    sys.modules.pop(name, None)


def _driver(tmp_path: Path) -> HammerDriver:
    return HammerDriver(HammerDriverOptions(environment_configs=[], project_configs=[str(tmp_path / "config.json")],
                                            log_file=str(tmp_path / "log.txt"), obj_dir=str(tmp_path / "obj")))


def test_a_path_repeated_in_every_library_is_resolved_a_few_times_not_per_library(design, monkeypatch) -> None:
    tmp_path, lib = design
    driver = _driver(tmp_path)
    calls: collections.Counter = collections.Counter()
    real = os.path.realpath

    def counting(path, *a, **k):
        calls[os.path.normpath(str(path))] += 1
        return real(path, *a, **k)

    monkeypatch.setattr(os.path, "realpath", counting)
    fp.stage_fingerprints(driver, CLIDriver(), "syn", [])
    assert 0 < calls[str(lib)] <= 3
