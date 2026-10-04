import ast
import hashlib
import os
import stat
import sys
import tempfile

import pytest

from hammer.vlsi import code_fingerprints as cf

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="the persistent memo needs POSIX ownership")

SRC = b"def f(a, b=2):\n    return a + b\n"
PLANTED = "0" * 64


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HAMMER_CODE_FP_CACHE", raising=False)
    monkeypatch.setattr(cf, "_DIGEST_MEMO", {})
    return tmp_path / "xdg" / "sledgehammer" / "ast"


@pytest.fixture
def parses(monkeypatch):
    calls = []
    parse = ast.parse

    def counting(*args, **kwargs):
        calls.append(args)
        return parse(*args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting)
    return calls


def _new_process(monkeypatch):
    monkeypatch.setattr(cf, "_DIGEST_MEMO", {})


def _entry(cache, data=SRC):
    return cache / f"{hashlib.sha256(data).hexdigest()}-{cf._MEMO_VERSION}"


def test_memo_hit_skips_parsing(cache, parses, monkeypatch):
    digest = cf.digest_source(SRC)
    assert len(parses) == 1
    assert _entry(cache).read_text() == digest
    _new_process(monkeypatch)
    assert cf.digest_source(SRC) == digest
    assert len(parses) == 1


def test_memo_dir_and_entries_are_private_under_any_umask(cache):
    old = os.umask(0)
    try:
        cf.digest_source(SRC)
    finally:
        os.umask(old)
    assert [stat.S_IMODE(d.stat().st_mode) for d in (cache, cache.parent, cache.parent.parent)] == [0o700] * 3
    assert stat.S_IMODE(_entry(cache).stat().st_mode) == 0o600
    assert [p.name for p in cache.iterdir()] == [_entry(cache).name]


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777])
def test_group_or_world_writable_memo_dir_is_neither_read_nor_written(cache, parses, monkeypatch, mode):
    digest = cf.digest_source(SRC)
    _entry(cache).write_text(PLANTED)
    cache.chmod(mode)
    _new_process(monkeypatch)
    try:
        assert cf.digest_source(SRC) == digest
        assert cf.digest_source(SRC + b"\n# other\n")
        assert len(parses) == 3
        assert _entry(cache).read_text() == PLANTED
        assert [p.name for p in cache.iterdir()] == [_entry(cache).name]
    finally:
        cache.chmod(0o700)


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_group_or_world_writable_entry_is_ignored_and_replaced(cache, parses, monkeypatch, mode):
    digest = cf.digest_source(SRC)
    _entry(cache).write_text(PLANTED)
    _entry(cache).chmod(mode)
    _new_process(monkeypatch)
    assert cf.digest_source(SRC) == digest
    assert len(parses) == 2
    assert _entry(cache).read_text() == digest
    assert stat.S_IMODE(_entry(cache).stat().st_mode) == 0o600


def test_memo_owned_by_another_user_is_neither_read_nor_written(cache, parses, monkeypatch):
    digest = cf.digest_source(SRC)
    _entry(cache).write_text(PLANTED)
    _new_process(monkeypatch)
    uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    assert cf.digest_source(SRC) == digest
    assert len(parses) == 2
    assert _entry(cache).read_text() == PLANTED


def test_entry_owned_by_another_user_is_ignored(cache, parses, monkeypatch):
    digest = cf.digest_source(SRC)
    _entry(cache).write_text(PLANTED)
    _new_process(monkeypatch)
    fstat = os.fstat

    def foreign(fd):
        st = fstat(fd)
        return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid + 1, st.st_gid,
                               st.st_size, int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))

    monkeypatch.setattr(os, "fstat", foreign)
    assert cf.digest_source(SRC) == digest
    assert len(parses) == 2


def test_symlinked_entry_is_ignored(cache, parses, monkeypatch, tmp_path):
    digest = cf.digest_source(SRC)
    decoy = tmp_path / "decoy"
    decoy.write_text(PLANTED)
    decoy.chmod(0o600)
    _entry(cache).unlink()
    _entry(cache).symlink_to(decoy)
    _new_process(monkeypatch)
    assert cf.digest_source(SRC) == digest
    assert len(parses) == 2
    assert not _entry(cache).is_symlink()


@pytest.mark.parametrize("corrupt", [
    lambda d: b"",
    lambda d: d[:63].encode(),
    lambda d: (d + "\n").encode(),
    lambda d: (d + "0").encode(),
    lambda d: d.upper().encode(),
    lambda d: b"g" * 64,
    lambda d: b"\xff" * 64,
    lambda d: ("raw:" + d).encode(),
], ids=["empty", "short", "newline", "long", "upper", "nonhex", "binary", "raw"])
def test_corrupted_entry_is_recomputed_and_rewritten(cache, parses, monkeypatch, corrupt):
    digest = cf.digest_source(SRC)
    _entry(cache).write_bytes(corrupt(digest))
    _new_process(monkeypatch)
    assert cf.digest_source(SRC) == digest
    assert len(parses) == 2
    assert _entry(cache).read_text() == digest


def test_serializer_version_change_misses(cache, parses, monkeypatch):
    digest = cf.digest_source(SRC)
    _new_process(monkeypatch)
    monkeypatch.setattr(cf, "_MEMO_VERSION", "f" * 64)
    assert cf.digest_source(SRC) == digest
    assert len(parses) == 2
    assert len(list(cache.iterdir())) == 2


def test_serializer_version_follows_this_module_source_and_the_python_minor(tmp_path, monkeypatch):
    with open(cf.__file__, "rb") as f:
        src = f.read()
    tag = f"{sys.implementation.name}-{sys.version_info[0]}.{sys.version_info[1]}\n".encode("ascii")
    assert cf._MEMO_VERSION == hashlib.sha256(tag + src).hexdigest()
    edited = tmp_path / "code_fingerprints.py"
    edited.write_bytes(src + b"\n")
    assert cf._source_version(str(edited)) not in (None, cf._MEMO_VERSION)
    with monkeypatch.context() as m:
        m.setattr(sys, "version_info", (sys.version_info[0], sys.version_info[1] + 1, 0, "final", 0))
        assert cf._source_version(cf.__file__) != cf._MEMO_VERSION


@pytest.mark.parametrize("value", ["off", "OFF", " off ", "0", "false", "no"])
def test_off_switch_writes_nothing(parses, monkeypatch, tmp_path, value):
    monkeypatch.setenv("HAMMER_CODE_FP_CACHE", value)
    cf.digest_source(SRC)
    _new_process(monkeypatch)
    cf.digest_source(SRC)
    assert len(parses) == 2
    assert not (tmp_path / "xdg").exists()
    assert not (tmp_path / "home").exists()


def test_memo_is_off_where_ownership_cannot_be_checked(parses, monkeypatch, tmp_path):
    monkeypatch.delattr(os, "getuid")
    cf.digest_source(SRC)
    _new_process(monkeypatch)
    cf.digest_source(SRC)
    assert len(parses) == 2
    assert not (tmp_path / "xdg").exists()


def test_unparsable_source_is_not_stored(cache):
    assert cf.digest_source(b"def f(:\n").startswith("raw:")
    assert not cache.exists() or not any(cache.iterdir())


def test_unusable_cache_location_falls_back_to_computing(parses, monkeypatch, tmp_path):
    (tmp_path / "xdg").write_text("not a directory\n")
    digest = cf.digest_source(SRC)
    _new_process(monkeypatch)
    assert cf.digest_source(SRC) == digest
    assert len(parses) == 2


def test_relative_xdg_cache_home_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", "relative-cache")
    monkeypatch.chdir(tmp_path)
    cf.digest_source(SRC)
    assert not (tmp_path / "relative-cache").exists()
    assert _entry(tmp_path / "home" / ".cache" / "sledgehammer" / "ast").is_file()


def test_no_write_lands_outside_the_cache_dir(cache, monkeypatch, tmp_path):
    for name in ("home", "cwd"):
        (tmp_path / name).mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    for attr in ("_FIRST", "_SNAPSHOT_DIGEST", "_IMPORTS"):
        monkeypatch.setattr(cf, attr, {})
    writes = []
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT

    def spy(name, real, is_write=lambda *a: True):
        def wrapper(*args, **kwargs):
            if is_write(*args, **kwargs):
                writes.append((name, os.path.abspath(args[-1] if name in ("replace", "rename") else args[0])))
            return real(*args, **kwargs)
        return wrapper

    with monkeypatch.context() as m:
        m.setattr(os, "open", spy("open", os.open, lambda path, flags, *a, **k: flags & write_flags))
        for name in ("mkdir", "replace", "rename"):
            m.setattr(os, name, spy(name, getattr(os, name)))
        m.setattr(tempfile, "tempdir", str(tmp_path / "elsewhere"))
        cf.digest_source(SRC)
        cf.framework_lines()
        cf.tool_lines("hammer.par.innovus")

    xdg = str(tmp_path / "xdg")
    inside = str(cache) + os.sep
    assert any(path.startswith(inside) for _, path in writes)
    assert all(path.startswith(inside) or (name == "mkdir" and (str(cache) + os.sep).startswith(path + os.sep)
                                           and path.startswith(xdg))
               for name, path in writes), writes
    created = sorted(os.path.relpath(os.path.join(d, f), str(tmp_path))
                     for d, _, files in os.walk(str(tmp_path)) for f in files)
    assert created and all(p.startswith(os.path.join("xdg", "sledgehammer", "ast") + os.sep) for p in created)


def test_warm_memo_gives_the_same_framework_and_tool_lines_without_parsing(monkeypatch):
    for attr in ("_FIRST", "_SNAPSHOT_DIGEST"):
        monkeypatch.setattr(cf, attr, {})
    cold = cf.framework_lines() + cf.tool_lines("hammer.par.innovus")
    for attr in ("_FIRST", "_SNAPSHOT_DIGEST", "_DIGEST_MEMO"):
        monkeypatch.setattr(cf, attr, {})
    calls = []
    parse = ast.parse
    monkeypatch.setattr(ast, "parse", lambda *a, **k: calls.append(a) or parse(*a, **k))
    assert cf.framework_lines() + cf.tool_lines("hammer.par.innovus") == cold
    assert calls == []


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="needs POSIX ownership")
@pytest.mark.parametrize("mode", [0o755, 0o711, 0o750])
def test_readable_memo_dir_is_tightened_to_private(tmp_path, mode):
    memo = tmp_path / "xdg" / "sledgehammer" / "ast"
    memo.mkdir(parents=True)
    memo.chmod(mode)
    cf.digest_source(b"x = 1\n")
    assert stat.S_IMODE(memo.stat().st_mode) == 0o700
    assert len([p for p in memo.iterdir() if not p.name.startswith(".tmp-")]) == 1
