import os
import sys
from pathlib import Path

import pytest

from hammer.vlsi import design_share

from tests.regression.test_a_shared_design_carries_the_files_its_hooks_name import (  # noqa: F401
    FakeDB, TCL, db, tool_runs, _alice_design, _build, _main, _share_and_pull, _syn)

DRIVER = '''import os
from typing import List

from hammer.vlsi import CLIDriver, HammerTool, HammerToolHookAction

from hooks.syn import syn_extra

SITE_TCL = {site!r}


class ProjectDriver(CLIDriver):
    def get_extra_synthesis_hooks(self) -> List[HammerToolHookAction]:
        return [HammerTool.make_pre_insertion_hook("step1", syn_extra)]


if __name__ == "__main__":
    ProjectDriver().main()
'''
HOOK = '''import os

from hammer.vlsi import HammerTool

{defs}

def syn_extra(x: HammerTool) -> bool:
    with open({path}) as f:
        text = f.read()
    with open(os.path.join(x.run_dir, "syn_extra.used"), "w") as f:
        f.write(text)
    return True
'''
PATHS = 'import os\n\nHERE = os.path.dirname(os.path.abspath(__file__))\nTCL_DIR = os.path.join(HERE, "tcl")\n'
# How hooks/syn.py names its file: (its definitions, open()'s argument, the file under hooks/).
LAYOUTS = {
    "beside": ("from .paths import HERE\n", 'os.path.join(HERE, "syn_extra.tcl")', "syn_extra.tcl"),
    "dir": ('from .paths import TCL_DIR\n\nSYN_TCL = os.path.join(TCL_DIR, "syn_extra.tcl")\n', "SYN_TCL",
            "tcl/syn_extra.tcl"),
}


def _forget_hooks() -> None:
    for name in [n for n in sys.modules if n == "hooks" or n.startswith("hooks.")]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def no_hooks_package():
    """No hooks package from another copy of the project stays imported."""
    _forget_hooks()
    yield
    _forget_hooks()


def _project(tmp_path: Path, layout: str) -> Path:
    """Alice's project, with its syn hook in a package beside the driver that reads a file under hooks/."""
    defs, path, tcl = LAYOUTS[layout]
    work = _alice_design(tmp_path, driver=DRIVER)
    (work / "hooks" / tcl).parent.mkdir(parents=True)
    (work / "hooks" / "__init__.py").write_text("")
    (work / "hooks" / "paths.py").write_text(PATHS)
    (work / "hooks" / "syn.py").write_text(HOOK.format(defs=defs, path=path))
    (work / "hooks" / tcl).write_text(TCL)
    return work


def _fresh_main(driver_file: str, args, out: Path, monkeypatch) -> int:
    """The driver as a fresh `python <driver>` runs it: its directory first on sys.path, nothing imported yet."""
    monkeypatch.syspath_prepend(os.path.dirname(driver_file))
    _forget_hooks()
    return _main(driver_file, args, out, monkeypatch)


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_the_bundle_holds_the_hook_modules_the_driver_imports(layout, tmp_path, db, monkeypatch) -> None:
    alice = _project(tmp_path, layout)
    bundle = design_share.bundle_from_dag(_build(alice, tmp_path, monkeypatch, _fresh_main), owner="alice")
    files = bundle.manifest["files"]
    for name in ("__init__.py", "paths.py", "syn.py", LAYOUTS[layout][2]):
        assert files[f"work/hooks/{name}"] == design_share._sha256(str(alice / "hooks" / name))
    assert {"work/hooks/__init__.py", "work/hooks/paths.py", "work/hooks/syn.py"} <= set(bundle.manifest["rewrite"])


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_a_partner_restores_the_owners_syn_when_its_hook_modules_read_a_file(layout, tmp_path, db, tool_runs,
                                                                             monkeypatch) -> None:
    alice = _project(tmp_path, layout)
    dag_file = _build(alice, tmp_path, monkeypatch, _fresh_main)
    assert _syn(dag_file, tmp_path, monkeypatch, _fresh_main) == 0
    assert tool_runs == [str(alice)] and len(db.blobs) == 1
    alices_keys = list(db.blobs)

    into = tmp_path / "bob" / "pulled"
    summary = _share_and_pull(db, dag_file, into)
    db.loads.clear()
    assert _syn(summary["dag_file"], tmp_path, monkeypatch, _fresh_main) == 0
    assert db.loads == alices_keys
    assert tool_runs == [str(alice)]


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_the_pulled_hook_module_finds_its_file_when_the_partners_syn_runs(layout, tmp_path, db, tool_runs,
                                                                          monkeypatch) -> None:
    alice = _project(tmp_path, layout)
    into = tmp_path / "bob" / "pulled"
    summary = _share_and_pull(db, _build(alice, tmp_path, monkeypatch, _fresh_main), into)
    assert _syn(summary["dag_file"], tmp_path, monkeypatch, _fresh_main) == 0
    assert tool_runs == [str(into)]
    assert (into / "build" / "dummy" / "syn-rundir" / "syn_extra.used").read_text() == TCL
