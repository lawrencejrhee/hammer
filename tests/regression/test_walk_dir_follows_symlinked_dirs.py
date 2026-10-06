"""Files reached through a symlinked subdirectory count, and a link loop still ends."""
import os

from hammer.vlsi import fingerprints as fp


def test_an_edit_under_a_symlinked_subdir_changes_the_lines(tmp_path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "defs.vh").write_text("`define W 8\n")
    inc = tmp_path / "inc"
    inc.mkdir()
    os.symlink(shared, inc / "common")

    before = fp.walk_dir(str(inc))
    assert any("defs.vh" in line for line in before)
    (shared / "defs.vh").write_text("`define W 16\n")
    assert fp.walk_dir(str(inc)) != before


def test_a_symlink_loop_is_walked_once(tmp_path) -> None:
    inc = tmp_path / "inc"
    (inc / "sub").mkdir(parents=True)
    (inc / "sub" / "a.vh").write_text("x\n")
    os.symlink(inc, inc / "sub" / "back")
    os.symlink(inc / "sub", inc / "again")

    lines = fp.walk_dir(str(inc))
    assert len([line for line in lines if "a.vh" in line]) == 1


def test_a_pruned_symlinked_subdir_stays_out(tmp_path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    (build / "out.v").write_text("x\n")
    inc = tmp_path / "inc"
    inc.mkdir()
    os.symlink(build, inc / "build")
    lines = fp.walk_dir(str(inc), prune=lambda d: os.path.basename(d) == "build")
    assert not any("out.v" in line for line in lines)
