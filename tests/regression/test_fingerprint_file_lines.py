import os
import sys

import pytest

from hammer.vlsi import fingerprints as fp


def _roots(tmp_path, obj="obj"):
    return fp.make_roots(str(tmp_path / obj / "tech-cache"), str(tmp_path / obj), hammer_root="")


def test_build_dir_files_hash_by_content_and_ignore_location(tmp_path):
    for obj in ("objA", "objB"):
        (tmp_path / obj).mkdir()
        (tmp_path / obj / "macro.lef").write_text("MACRO A\n")
    a = fp.file_line(str(tmp_path / "objA" / "macro.lef"), _roots(tmp_path, "objA"))
    b = fp.file_line(str(tmp_path / "objB" / "macro.lef"), _roots(tmp_path, "objB"))
    assert a == b
    assert a.startswith("<OBJ_DIR>/macro.lef:sha256=")


def test_rewrite_with_same_bytes_keeps_the_line_under_a_root(tmp_path):
    cache = tmp_path / "obj" / "tech-cache"
    cache.mkdir(parents=True)
    f = cache / "stdcells.lef"
    f.write_text("MACRO A\n")
    roots = _roots(tmp_path)
    before = fp.file_line(str(f), roots)
    st = f.stat()
    f.write_text("MACRO A\n")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert fp.file_line(str(f), roots) == before
    assert before.startswith("<TECH_CACHE>/stdcells.lef:sha256=")
    f.write_text("MACRO B\n")
    assert fp.file_line(str(f), roots) != before


def test_tech_cache_inside_obj_dir_gets_the_longer_token(tmp_path):
    roots = _roots(tmp_path)
    assert roots[0][1] == "<TECH_CACHE>"
    assert fp.tokenize(str(tmp_path / "obj" / "tech-cache" / "x.lib"), roots) == "<TECH_CACHE>/x.lib"
    assert fp.tokenize(str(tmp_path / "obj" / "x.lib"), roots) == "<OBJ_DIR>/x.lib"
    assert fp.tokenize(str(tmp_path / "elsewhere.lib"), roots) is None


def test_external_files_are_stat_lines_with_ctime(tmp_path):
    f = tmp_path / "pdk.lib"
    f.write_text("library(x) {}\n")
    line = fp.file_line(str(f), _roots(tmp_path))
    st = f.stat()
    assert line == f"{os.path.normpath(str(f))}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}"


def test_missing_and_bad_paths_are_stable_lines(tmp_path):
    roots = _roots(tmp_path)
    assert fp.file_line(str(tmp_path / "obj" / "gone.lef"), roots) == "MISSING:<OBJ_DIR>/gone.lef"
    assert fp.file_line(str(tmp_path / "a.lef" / "under_a_file"), roots).startswith("MISSING:")
    assert fp.file_line("/bad\0path.lef", roots).startswith("MISSING:")


def test_directory_is_not_a_file_line(tmp_path):
    assert fp.file_line(str(tmp_path), ()).startswith("NOTFILE:")


def test_large_files_under_a_root_use_size_and_whole_second_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "CONTENT_HASH_LIMIT", 4)
    (tmp_path / "obj").mkdir()
    f = tmp_path / "obj" / "big.gds"
    f.write_text("0123456789")
    st = f.stat()
    assert fp.file_line(str(f), _roots(tmp_path)) == f"<OBJ_DIR>/big.gds:{st.st_size}:{st.st_mtime_ns // 10**9}"


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0,
                    reason="needs POSIX permissions and a non-root user")
def test_unreadable_file_under_a_root(tmp_path):
    (tmp_path / "obj").mkdir()
    f = tmp_path / "obj" / "secret.lib"
    f.write_text("x")
    f.chmod(0)
    try:
        assert fp.file_line(str(f), _roots(tmp_path)).startswith("UNREADABLE:<OBJ_DIR>/secret.lib:")
    finally:
        f.chmod(0o644)


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_symlink_into_a_root_is_content_hashed(tmp_path):
    (tmp_path / "obj").mkdir()
    target = tmp_path / "obj" / "real.lef"
    target.write_text("MACRO A\n")
    link = tmp_path / "link.lef"
    link.symlink_to(target)
    assert fp.file_line(str(link), _roots(tmp_path)) == fp.file_line(str(target), _roots(tmp_path))


def test_memo_reuses_a_line_within_one_computation(tmp_path):
    f = tmp_path / "a.lef"
    f.write_text("x")
    memo = {}
    first = fp.file_line(str(f), (), memo)
    f.write_text("longer content")
    assert fp.file_line(str(f), (), memo) == first
    assert fp.file_line(str(f), ()) != first


def test_walk_dir_skips_artefacts_and_caps(tmp_path):
    d = tmp_path / "ilm"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "__pycache__").mkdir()
    for name in ("a.lef", "sub/b.gds", ".hidden", "a.lef~", "#a.lef#", "x.swp", "notes.md", "README",
                 "out.tmp.123.4", "sub/__pycache__/m.pyc"):
        (d / name).write_text(name)
    lines = fp.walk_dir(str(d))
    assert sorted(os.path.basename(line.split(":")[0]) for line in lines) == ["a.lef", "b.gds"]
    assert fp.walk_dir(str(d), recursive=False) == [fp.file_line(str(d / "a.lef"))]
    capped = fp.walk_dir(str(d), cap=1)
    assert len(capped) == 1 and capped[0].startswith("DIRSTAT:") and capped[0].split(":")[-4] == "2"


def test_walk_of_a_missing_dir_is_a_missing_line(tmp_path):
    assert fp.walk_dir(str(tmp_path / "nope")) == [f"MISSING:{os.path.normpath(str(tmp_path / 'nope'))}"]


def test_digest_ignores_line_order():
    assert fp.digest_lines(["b", "a"]) == fp.digest_lines(["a", "b"])
    assert fp.digest_lines(["a"]) != fp.digest_lines(["a", "b"])


def test_hammer_root_without_a_package_file():
    root = fp.hammer_dir()
    assert os.path.isfile(os.path.join(root, "vlsi", "fingerprints.py"))
    assert fp.tokenize(fp.__file__, fp.make_roots(None, None)) == "<HAMMER>/vlsi/fingerprints.py"


def test_debug_record_appends_scoped_lines(tmp_path, monkeypatch):
    out = tmp_path / "debug.txt"
    monkeypatch.setenv(fp.DEBUG_ENV, str(out))
    fp.debug_record("lvs", ["b", "a"])
    assert out.read_text() == "lvs\ta\nlvs\tb\n"
