import errno
import io
import os
import stat
import tarfile

import pytest

from hammer.vlsi.pd_store import tar_directory, untar_to_directory


@pytest.fixture
def no_filters(monkeypatch):
    monkeypatch.delattr(tarfile, "tar_filter", raising=False)


@pytest.fixture
def not_root():
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")


def _tar_of(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, payload in members:
            if payload is not None:
                info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload) if payload is not None else None)
    return buf.getvalue()


def _dir(name, mode=0o755, mtime=0):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = mode
    info.mtime = mtime
    return info, None


def _file(name, payload=b"x"):
    return tarfile.TarInfo(name), payload


def _link(name, target, kind):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info, None


def _symlink(name, target):
    return _link(name, target, tarfile.SYMTYPE)


def _hard_link(name, target):
    return _link(name, target, tarfile.LNKTYPE)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_read_only_directory_restores(tmp_path, no_filters, not_root):
    blob = _tar_of([_dir("rundir"), _dir("rundir/ro", 0o555), _file("rundir/ro/data.txt", b"cached\n")])
    untar_to_directory(blob, tmp_path / "dest")
    ro = tmp_path / "dest" / "rundir" / "ro"
    assert (ro / "data.txt").read_text() == "cached\n"
    assert _mode(ro) == 0o555
    os.chmod(ro, 0o755)


def test_a_directory_without_owner_search_permission_keeps_its_subdirectories(tmp_path, no_filters, not_root):
    blob = _tar_of([_dir("rundir"), _dir("rundir/closed", 0o644), _dir("rundir/closed/sub"),
                    _file("rundir/closed/sub/data.txt")])
    untar_to_directory(blob, tmp_path / "dest")
    closed = tmp_path / "dest" / "rundir" / "closed"
    assert _mode(closed) == 0o644
    os.chmod(closed, 0o755)
    assert (closed / "sub" / "data.txt").read_bytes() == b"x"


def test_directory_mtimes_are_restored(tmp_path, no_filters):
    blob = _tar_of([_dir("rundir"), _dir("rundir/sub", mtime=1000000000), _file("rundir/sub/data.txt")])
    untar_to_directory(blob, tmp_path / "dest")
    assert os.stat(tmp_path / "dest" / "rundir" / "sub").st_mtime == 1000000000


def test_directory_modes_are_masked(tmp_path, no_filters):
    blob = _tar_of([_dir("rundir"), _dir("rundir/shared", 0o3777), _file("rundir/shared/data.txt")])
    untar_to_directory(blob, tmp_path / "dest")
    assert _mode(tmp_path / "dest" / "rundir" / "shared") == 0o755


def test_absolute_symlinks_restore_under_a_default_data_filter(tmp_path, no_filters, monkeypatch):
    if not hasattr(tarfile, "data_filter"):
        pytest.skip("this Python has no tarfile data filter")
    monkeypatch.setattr(tarfile.TarFile, "extraction_filter",
                        staticmethod(tarfile.data_filter), raising=False)
    producer = tmp_path / "producer"
    target = producer / "par-rundir" / "pre_place"
    rundir = producer / "par-rundir"
    (rundir / "pre_place").mkdir(parents=True)
    (rundir / "pre_place" / "db.txt").write_text("checkpoint\n")
    os.symlink(str(target), str(rundir / "post_place"))
    blob = tar_directory(rundir, arcname="par-rundir")

    consumer = tmp_path / "consumer"
    untar_to_directory(blob, consumer)
    link = consumer / "par-rundir" / "post_place"
    assert os.path.islink(link)
    assert os.readlink(link) == str(target)


def test_dotdot_member_is_refused_without_filters(tmp_path, no_filters):
    dest = tmp_path / "a" / "dest"
    dest.mkdir(parents=True)
    with pytest.raises(tarfile.ExtractError):
        untar_to_directory(_tar_of([_file("rundir/ok.txt"), _file("../escaped.txt")]), dest)
    assert not list(tmp_path.rglob("escaped.txt"))


def test_absolute_name_is_refused_without_filters(tmp_path, no_filters):
    with pytest.raises(tarfile.ExtractError):
        untar_to_directory(_tar_of([_file("/etc/escaped.txt")]), tmp_path / "dest")


def test_hard_link_out_is_refused_without_filters(tmp_path, no_filters):
    (tmp_path / "secret.txt").write_text("secret\n")
    with pytest.raises(tarfile.ExtractError):
        untar_to_directory(_tar_of([_hard_link("rundir/stolen", "../../secret.txt")]), tmp_path / "dest")
    assert not (tmp_path / "dest" / "rundir" / "stolen").exists()


def test_writing_through_an_escaping_symlink_is_refused_without_filters(tmp_path, no_filters):
    outside = tmp_path / "outside"
    outside.mkdir()
    blob = _tar_of([_symlink("rundir/out", str(outside)), _file("rundir/out/pwned.txt")])
    with pytest.raises(tarfile.ExtractError):
        untar_to_directory(blob, tmp_path / "dest")
    assert not (outside / "pwned.txt").exists()


@pytest.mark.parametrize("member", [
    _file("rundir/out/pwned.txt"),
    _hard_link("rundir/stolen", "rundir/out/secret.txt"),
    _dir("rundir/out", 0o555),
    _file("rundir/new/../out/sub/pwned.txt"),
], ids=["file", "hard_link", "directory", "dotdot"])
def test_symlinks_are_refused_even_where_realpath_misses_them(tmp_path, no_filters, monkeypatch, member):
    monkeypatch.setattr(os.path, "realpath", os.path.abspath)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o700)
    (outside / "secret.txt").write_text("secret\n")
    blob = _tar_of([_symlink("rundir/out", str(outside)), member])
    with pytest.raises(tarfile.ExtractError):
        untar_to_directory(blob, tmp_path / "dest")
    assert [p.name for p in outside.iterdir()] == ["secret.txt"]
    assert _mode(outside) == 0o700


def test_a_path_that_cannot_be_checked_is_refused(tmp_path, no_filters, monkeypatch):
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        if str(path).endswith("/rundir/deep"):
            raise OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG), path)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    blob = _tar_of([_file("rundir/deep/a.txt"), _file("rundir/deep/b.txt")])
    with pytest.raises(OSError) as refused:
        untar_to_directory(blob, tmp_path / "dest")
    assert refused.value.errno == errno.ENAMETOOLONG
    assert not (tmp_path / "dest" / "rundir").exists()


def test_a_later_symlink_cannot_take_a_directorys_deferred_mode(tmp_path, no_filters):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o700)
    blob = _tar_of([_dir("rundir"), _dir("rundir/d", 0o555), _symlink("rundir/d", str(outside))])
    try:
        untar_to_directory(blob, tmp_path / "dest")
    except (tarfile.TarError, OSError):
        pass
    assert _mode(outside) == 0o700
