import os

from hammer.vlsi.pd_store import compute_collateral_fingerprint


def _config(tmp_path, lef, rtl):
    return {
        "vlsi.technology.extra_libraries": [
            {"library": {"lef_file": str(lef)}},
        ],
        "synthesis.inputs.input_files": [str(rtl)],
        "par.outputs.output_gds": str(tmp_path / "build" / "out.gds"),
        "some.unrelated.key": 42,
    }


def test_library_edit_changes_fingerprint(tmp_path):
    lef = tmp_path / "cells.lef"
    rtl = tmp_path / "top.v"
    lef.write_text("MACRO A\n")
    rtl.write_text("module top; endmodule\n")
    cfg = _config(tmp_path, lef, rtl)

    before = compute_collateral_fingerprint(cfg)
    lef.write_text("MACRO A\nMACRO B\n")
    after = compute_collateral_fingerprint(cfg)
    assert before != after


def test_touch_without_change_also_invalidates(tmp_path):
    # stat-based on purpose: a touched library invalidates even with the same
    # bytes. Correctness over hit rate.
    lef = tmp_path / "cells.lef"
    rtl = tmp_path / "top.v"
    lef.write_text("MACRO A\n")
    rtl.write_text("module top; endmodule\n")
    cfg = _config(tmp_path, lef, rtl)

    before = compute_collateral_fingerprint(cfg)
    st = lef.stat()
    os.utime(lef, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    after = compute_collateral_fingerprint(cfg)
    assert before != after


def test_rtl_and_obj_dir_excluded(tmp_path):
    lef = tmp_path / "cells.lef"
    rtl = tmp_path / "top.v"
    out = tmp_path / "build" / "out.gds"
    lef.write_text("MACRO A\n")
    rtl.write_text("module top; endmodule\n")
    out.parent.mkdir()
    out.write_text("gds")
    cfg = _config(tmp_path, lef, rtl)

    kwargs = dict(exclude_files={str(rtl)},
                  exclude_prefixes=(str(tmp_path / "build"),))
    before = compute_collateral_fingerprint(cfg, **kwargs)
    rtl.write_text("module top2; endmodule\n")
    out.write_text("gds v2 with different length")
    after = compute_collateral_fingerprint(cfg, **kwargs)
    assert before == after


def test_missing_file_is_stable_until_it_appears(tmp_path):
    lef = tmp_path / "not_yet.lef"
    cfg = {"tech.lef": str(lef)}
    a = compute_collateral_fingerprint(cfg)
    b = compute_collateral_fingerprint(cfg)
    assert a == b
    lef.write_text("MACRO A\n")
    c = compute_collateral_fingerprint(cfg)
    assert c != a


def test_relative_and_non_collateral_strings_ignored(tmp_path):
    cfg = {
        "a": "relative/cells.lef",          # not absolute -> ignored
        "b": "/definitely/missing/notes.txt",  # wrong extension -> ignored
    }
    assert (compute_collateral_fingerprint(cfg)
            == compute_collateral_fingerprint({}))


def test_extra_files_are_fingerprinted(tmp_path):
    lib = tmp_path / "cells_tt.lib"
    lib.write_text("library(cells_tt) {}\n")
    cfg = {"unrelated": True}
    before = compute_collateral_fingerprint(cfg, extra_files=[str(lib)])
    lib.write_text("library(cells_tt) { cell(BUF) {} }\n")
    after = compute_collateral_fingerprint(cfg, extra_files=[str(lib)])
    assert before != after
    assert before != compute_collateral_fingerprint(cfg)
