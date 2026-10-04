import os
import sys

import pytest

from hammer.vlsi import fingerprints as fp
from hammer.vlsi import pd_store

MTIME_NS = 1791091939649747839


def _write(path, data, mtime_ns=MTIME_NS):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def _roots(obj_dir):
    return fp.make_roots(None, str(obj_dir), hammer_root="")


def _restore(src_dir, dest_parent):
    pd_store.untar_to_directory(pd_store.tar_directory(src_dir), dest_parent)
    return dest_parent / src_dir.name


def test_a_file_too_big_to_hash_keeps_its_line_through_a_tar_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "CONTENT_HASH_LIMIT", 16)
    obj_a, obj_b = tmp_path / "objA", tmp_path / "objB"
    big = _write(obj_a / "syn-rundir" / "big.v", b"module big; endmodule\n" * 10)
    restored = _restore(big.parent, obj_b) / "big.v"
    line_a = fp.file_line(str(big), _roots(obj_a))
    assert line_a == f"<OBJ_DIR>/syn-rundir/big.v:220:{MTIME_NS // 10**9}"
    assert fp.file_line(str(restored), _roots(obj_b)) == line_a


def test_a_build_dir_summary_keeps_its_line_through_a_tar_round_trip(tmp_path):
    obj_a, obj_b = tmp_path / "objA", tmp_path / "objB"
    for name in ("a.lef", "b.lef", "c.lef"):
        _write(obj_a / "par-rundir" / "ilm" / name, b"MACRO m\n", MTIME_NS + len(name))
    restored = _restore(obj_a / "par-rundir", obj_b)
    [line_a] = fp.walk_dir(str(obj_a / "par-rundir" / "ilm"), _roots(obj_a), cap=1)
    assert line_a.startswith("DIRSTAT:<OBJ_DIR>/par-rundir/ilm:3:")
    assert fp.walk_dir(str(restored / "ilm"), _roots(obj_b), cap=1) == [line_a]


def test_a_summary_outside_the_roots_still_carries_ctime(tmp_path):
    for name in ("a.lef", "b.lef"):
        _write(tmp_path / "pdk" / name, b"MACRO m\n")
    [line] = fp.walk_dir(str(tmp_path / "pdk"), (), cap=1)
    ctime = max(os.stat(tmp_path / "pdk" / n).st_ctime_ns for n in ("a.lef", "b.lef"))
    assert line == f"DIRSTAT:{os.path.normpath(tmp_path / 'pdk')}:2:{MTIME_NS}:{ctime}:16"


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs POSIX permissions and a non-root user")
def test_an_unreadable_build_dir_file_is_keyed_by_whole_seconds(tmp_path):
    obj = tmp_path / "obj"
    locked = _write(obj / "syn-rundir" / "netlist.v", b"module m; endmodule\n")
    locked.chmod(0)
    try:
        line = fp.file_line(str(locked), _roots(obj))
    finally:
        locked.chmod(0o644)
    assert line == f"UNREADABLE:<OBJ_DIR>/syn-rundir/netlist.v:20:{MTIME_NS // 10**9}"
