import pytest

from hammer.vlsi import rtl_check

TOP = "module top(input [`W-1:0] a, output [`W-1:0] b);\n  assign b = a + `V;\nendmodule\n"


@pytest.fixture
def design(tmp_path, monkeypatch):
    """top.v includes "defs.vh" (found in incB) and <ang.vh>; counts slang elaborations."""
    try:
        rtl_check._checked_binary()
    except rtl_check.SlangNotFound as e:
        pytest.skip(f"needs slang {rtl_check.SLANG_VERSION}: {e}")
    monkeypatch.setenv("HAMMER_RTL_FP_CACHE", str(tmp_path / "memo"))
    for d in ("src", "incA", "incB"):
        (tmp_path / d).mkdir()
    (tmp_path / "incB" / "defs.vh").write_text("`define W 8\n")
    (tmp_path / "incB" / "ang.vh").write_text("`define V 1\n")
    (tmp_path / "src" / "top.v").write_text('`include "defs.vh"\n`include <ang.vh>\n' + TOP)
    runs = []
    real = rtl_check._run_slang

    def counting(*a, **k):
        runs.append(1)
        return real(*a, **k)

    monkeypatch.setattr(rtl_check, "_run_slang", counting)

    def fp(include_dirs=None):
        dirs = include_dirs if include_dirs is not None else [str(tmp_path / "incA"), str(tmp_path / "incB")]
        return rtl_check.digest_units([str(tmp_path / "src" / "top.v")], include_dirs=dirs, top_module="top")[0]

    return tmp_path, runs, fp


def test_second_run_is_a_memo_hit(design) -> None:
    _, runs, fp = design
    first = fp()
    assert fp() == first
    assert len(runs) == 1


def test_editing_a_quoted_include_misses(design) -> None:
    tmp, runs, fp = design
    first = fp()
    (tmp / "incB" / "defs.vh").write_text("`define W 16\n")
    assert fp() != first
    assert len(runs) == 2


def test_editing_an_angle_include_misses(design) -> None:
    tmp, runs, fp = design
    first = fp()
    (tmp / "incB" / "ang.vh").write_text("`define V 2\n")
    assert fp() != first
    assert len(runs) == 2


@pytest.mark.parametrize("spelling", ["`include<ang.vh>", "`include /* hdr */ <ang.vh>"])
def test_includes_spelled_without_a_space_are_tracked(design, spelling) -> None:
    tmp, runs, fp = design
    top = tmp / "src" / "top.v"
    top.write_text(top.read_text().replace("`include <ang.vh>", spelling))
    first = fp()
    (tmp / "incB" / "ang.vh").write_text("`define V 2\n")
    assert fp() != first
    assert len(runs) == 2


def test_a_new_header_that_shadows_the_old_one_misses(design) -> None:
    tmp, runs, fp = design
    first = fp()
    (tmp / "incA" / "defs.vh").write_text("`define W 4\n")
    assert fp() != first
    assert len(runs) == 2


@pytest.mark.parametrize("top", [
    '`define HDR "defs.vh"\n`include <ang.vh>\n`include `HDR\n' + TOP,
    '`define HDR "defs.vh"\n`include <ang.vh>\n`include`HDR\n' + TOP,
    '`define INC(f) `include f\n`INC("defs.vh")\n`INC(<ang.vh>)\n' + TOP,
])
def test_includes_named_through_a_macro_are_tracked(design, top) -> None:
    tmp, runs, fp = design
    (tmp / "src" / "top.v").write_text(top)
    first = fp()
    assert fp() == first
    (tmp / "incB" / "defs.vh").write_text("`define W 16\n")
    second = fp()
    assert second != first
    (tmp / "incB" / "ang.vh").write_text("`define V 2\n")
    assert fp() != second
    assert len(runs) == 3


@pytest.mark.parametrize("top", [
    '`include "defs.vh"\n`define MSG "/*"\n`include <ang.vh>\n/* end */\n' + TOP,
    '`include "defs.vh"\nmodule top(input [`W-1:0] a, output [`W-1:0] b);\n  initial $display("/*");\n'
    '`include <ang.vh>\n  assign b = a + `V; /* note */\nendmodule\n',
])
def test_a_comment_marker_in_a_string_hides_no_include(design, top) -> None:
    tmp, runs, fp = design
    (tmp / "src" / "top.v").write_text(top)
    first = fp()
    (tmp / "incB" / "ang.vh").write_text("`define V 2\n")
    assert fp() != first
    assert len(runs) == 2


def test_an_include_path_only_slang_normalizes_is_tracked(design) -> None:
    tmp, runs, fp = design
    top = tmp / "src" / "top.v"
    top.write_text(top.read_text().replace("<ang.vh>", "<gen/../ang.vh>"))
    first = fp()
    (tmp / "incB" / "ang.vh").write_text("`define V 2\n")
    assert fp() != first
    assert len(runs) == 2


def test_a_header_found_through_a_glob_include_dir_is_tracked(design) -> None:
    tmp, runs, fp = design
    (tmp / "glob" / "x").mkdir(parents=True)
    (tmp / "glob" / "x" / "ang.vh").write_text("`define V 1\n")
    (tmp / "incB" / "ang.vh").unlink()
    dirs = [str(tmp / "incB"), str(tmp / "glob" / "*")]
    first = fp(dirs)
    (tmp / "glob" / "x" / "ang.vh").write_text("`define V 2\n")
    assert fp(dirs) != first
    assert len(runs) == 2


def test_a_header_in_an_inactive_branch_costs_no_rerun(design) -> None:
    tmp, runs, fp = design
    (tmp / "incB" / "sim.vh").write_text("`define SIM 1\n")
    top = tmp / "src" / "top.v"
    top.write_text("`ifndef SYNTHESIS\n`include <sim.vh>\n`endif\n" + top.read_text())
    first = fp()
    (tmp / "incB" / "sim.vh").write_text("`define SIM 2\n")
    assert fp() == first
    assert len(runs) == 1


def test_a_file_changed_during_the_run_is_not_memoized(design, monkeypatch) -> None:
    tmp, runs, fp = design
    slang = rtl_check._run_slang

    def edit_while_running(*a, **k):
        doc = slang(*a, **k)
        (tmp / "incB" / "defs.vh").write_text("`define W 16\n")
        return doc

    monkeypatch.setattr(rtl_check, "_run_slang", edit_while_running)
    stale = fp()
    monkeypatch.setattr(rtl_check, "_run_slang", slang)
    assert fp() != stale
    assert len(runs) == 2


def test_an_edit_undone_during_the_run_is_not_memoized(design, monkeypatch) -> None:
    tmp, runs, fp = design
    slang = rtl_check._run_slang
    defs = tmp / "incB" / "defs.vh"

    def edit_and_revert(*a, **k):
        defs.write_text("`define W 16\n")
        try:
            return slang(*a, **k)
        finally:
            defs.write_text("`define W 8\n")

    monkeypatch.setattr(rtl_check, "_run_slang", edit_and_revert)
    during = fp()
    monkeypatch.setattr(rtl_check, "_run_slang", slang)
    assert fp() != during
    assert len(runs) == 2


def test_a_slang_that_crashes_multi_threaded_still_memoizes(design, monkeypatch) -> None:
    import subprocess
    _, runs, fp = design
    real = subprocess.run

    def sigbus_unless_single_threaded(cmd, **kwargs):
        if "--threads" not in cmd and "--version" not in cmd:
            return subprocess.CompletedProcess(cmd, -7, "", "")
        return real(cmd, **kwargs)

    monkeypatch.setattr(rtl_check.subprocess, "run", sigbus_unless_single_threaded)
    first = fp()
    assert not first.startswith("bytes:")
    assert fp() == first
    assert len(runs) == 1


def test_other_defines_are_another_entry(design) -> None:
    tmp, runs, fp = design
    fp()
    rtl_check.digest_units([str(tmp / "src" / "top.v")], include_dirs=[str(tmp / "incA"), str(tmp / "incB")],
                           defines=["X=1"], top_module="top")
    assert len(runs) == 2


def test_memo_off(design, monkeypatch) -> None:
    _, runs, fp = design
    monkeypatch.setenv("HAMMER_RTL_FP_CACHE", "off")
    fp()
    fp()
    assert len(runs) == 2
