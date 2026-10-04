import os
import sys
from types import SimpleNamespace

import pytest

from hammer.vlsi.hammer_build_systems import _link_dag_into_dags_folder

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")


def _build(tmp_path, name: str) -> str:
    obj = tmp_path / name / "gcd"
    obj.mkdir(parents=True)
    (obj / "hammer_dag.py").write_text(f"# {name}\n")
    return str(obj / "hammer_dag.py")


@pytest.fixture
def link(tmp_path):
    dags = tmp_path / "dags"
    driver = SimpleNamespace(database=SimpleNamespace(get_setting=lambda key: str(dags)))
    path = dags / "sledgehammer_gcd_u.py"

    def do(dag_file: str):
        _link_dag_into_dags_folder(driver, dag_file, "sledgehammer_gcd_u")
        return path

    return do


def test_regular_file_is_moved_aside_not_deleted(tmp_path, link, capsys) -> None:
    (tmp_path / "dags").mkdir()
    (tmp_path / "dags" / "sledgehammer_gcd_u.py").write_text("my own copy\n")
    path = link(_build(tmp_path, "sky130"))
    assert os.path.islink(path)
    backups = [p for p in os.listdir(tmp_path / "dags") if ".replaced-" in p]
    assert len(backups) == 1 and not backups[0].endswith(".py")
    assert (tmp_path / "dags" / backups[0]).read_text() == "my own copy\n"
    assert "was not a link" in capsys.readouterr().out


def test_replacing_another_builds_dag_says_so(tmp_path, link, capsys) -> None:
    link(_build(tmp_path, "asap7"))
    capsys.readouterr()
    sky = _build(tmp_path, "sky130")
    path = link(sky)
    assert os.path.realpath(path) == os.path.realpath(sky)
    out = capsys.readouterr().out
    assert "now builds" in out and "asap7" in out


def test_relinking_the_same_build_is_quiet(tmp_path, link, capsys) -> None:
    sky = _build(tmp_path, "sky130")
    link(sky)
    capsys.readouterr()
    link(sky)
    assert "NOTE" not in capsys.readouterr().out


def test_dangling_link_is_replaced_quietly(tmp_path, link, capsys) -> None:
    (tmp_path / "dags").mkdir()
    os.symlink(str(tmp_path / "gone" / "hammer_dag.py"), tmp_path / "dags" / "sledgehammer_gcd_u.py")
    sky = _build(tmp_path, "sky130")
    assert os.path.realpath(link(sky)) == os.path.realpath(sky)
    assert "NOTE" not in capsys.readouterr().out
