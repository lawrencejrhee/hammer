import errno

import pytest

from hammer.vlsi import code_fingerprints as cf

NAMES = ["x.py", "x.yml", "x.tcl"]


@pytest.fixture(autouse=True)
def _fresh_digests(monkeypatch):
    monkeypatch.setattr(cf, "_FIRST", {})


def _raise(error):
    def boom(*args, **kwargs):
        raise error

    return boom


@pytest.mark.parametrize("name", NAMES)
def test_absent_paths_are_missing(tmp_path, name):
    (tmp_path / "file").write_text("x\n")
    (tmp_path / "loop").symlink_to("loop")
    paths = [tmp_path / "gone" / name, tmp_path / "file" / name, tmp_path / "loop" / name,
             tmp_path / ("a" * 300 + name)]
    assert [cf._file_digest(str(p)) for p in paths] == ["MISSING"] * len(paths)
    assert cf._file_digest(str(tmp_path) + "/x\0" + name) == "MISSING"


@pytest.mark.parametrize("name", NAMES)
def test_permission_denied_is_unreadable(tmp_path, monkeypatch, name):
    (tmp_path / name).write_text("x = 1\n")
    monkeypatch.setattr(cf, "_read", _raise(PermissionError(errno.EACCES, "denied")))
    assert cf._file_digest(str(tmp_path / name)) == "UNREADABLE"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("error", [OSError(errno.EIO, "io"), OSError(errno.ESTALE, "stale"), ValueError("bad")])
def test_io_errors_raise_instead_of_reading_as_missing(tmp_path, monkeypatch, name, error):
    (tmp_path / name).write_text("x = 1\n")
    monkeypatch.setattr(cf, "_read", _raise(error))
    with pytest.raises(type(error)):
        cf._file_digest(str(tmp_path / name))


def test_framework_snapshot_io_error_raises_but_a_deleted_file_is_inmem(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "_SNAPSHOT", {})
    monkeypatch.setattr(cf, "_SNAPSHOT_DIGEST", {})
    monkeypatch.setattr(cf, "_WARNED", set())
    (tmp_path / "vlsi").mkdir()
    mod = tmp_path / "vlsi" / "units.py"
    mod.write_text("X = 1\n")
    cf._take_snapshot(str(tmp_path), ["vlsi/units.py"])
    loaded = cf.code_digest(str(mod))
    with monkeypatch.context() as m:
        m.setattr(cf, "_read", _raise(OSError(errno.EIO, "io")))
        with pytest.raises(OSError):
            cf.code_digest(str(mod))
    mod.unlink()
    assert cf.code_digest(str(mod)) == "inmem:" + loaded
