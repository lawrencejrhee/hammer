import io
import os
import tarfile

import pytest

from hammer.vlsi.pd_store import tar_directory, untar_to_directory


def _rundir(root, link_target):
    """A rundir shaped like innovus leaves one: a checkpoint dir and a link to it."""
    rundir = root / "par-rundir"
    (rundir / "pre_place").mkdir(parents=True)
    (rundir / "pre_place" / "db.txt").write_text("checkpoint\n")
    (rundir / "output.json").write_text('{"stage": "par"}\n')
    os.symlink(str(link_target), str(rundir / "post_place"))
    return rundir


def _tar_of(names):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in names:
            payload = b"x"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_restoring_twice_succeeds(tmp_path):
    """A rundir's links point into the workspace that produced it. Restoring the
    same blob again must not trip the extraction filter on those links."""
    producer = tmp_path / "producer"
    rundir = _rundir(producer, producer / "par-rundir" / "pre_place")
    blob = tar_directory(rundir, arcname="par-rundir")

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    for _ in range(3):
        untar_to_directory(blob, consumer)

    restored = consumer / "par-rundir"
    assert (restored / "output.json").read_text() == '{"stage": "par"}\n'
    assert os.readlink(restored / "post_place") == str(
        producer / "par-rundir" / "pre_place")


def test_absolute_link_targets_survive(tmp_path):
    producer = tmp_path / "producer"
    target = producer / "par-rundir" / "pre_place"
    rundir = _rundir(producer, target)
    blob = tar_directory(rundir, arcname="par-rundir")

    consumer = tmp_path / "consumer"
    untar_to_directory(blob, consumer)
    link = consumer / "par-rundir" / "post_place"
    assert os.path.islink(link)
    assert os.readlink(link) == str(target)


def test_traversal_is_refused(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    outside = tmp_path / "escaped.txt"

    with pytest.raises(Exception):
        untar_to_directory(_tar_of(["rundir/ok.txt", "../escaped.txt"]), dest)
    assert not outside.exists()


def test_rejected_blob_leaves_dest_untouched(tmp_path):
    """Nothing lands unless the whole archive extracted, so a refused blob
    cannot half-replace an existing rundir."""
    dest = tmp_path / "dest"
    dest.mkdir()
    keep = dest / "existing.txt"
    keep.write_text("untouched\n")

    with pytest.raises(Exception):
        untar_to_directory(_tar_of(["rundir/ok.txt", "../escaped.txt"]), dest)

    assert keep.read_text() == "untouched\n"
    assert [p.name for p in dest.iterdir()] == ["existing.txt"]


def test_restore_replaces_previous_contents(tmp_path):
    producer = tmp_path / "producer"
    rundir = _rundir(producer, producer / "par-rundir" / "pre_place")
    blob = tar_directory(rundir, arcname="par-rundir")

    consumer = tmp_path / "consumer"
    stale = consumer / "par-rundir"
    stale.mkdir(parents=True)
    (stale / "stale.txt").write_text("from an older run\n")

    untar_to_directory(blob, consumer)
    assert not (stale / "stale.txt").exists()
    assert (stale / "output.json").exists()


# The same guarantees must hold on a Python without tarfile's extraction
# filters (macOS's system 3.9.6), where untar_to_directory checks by hand.

@pytest.fixture(params=["filter", "no_filter"])
def extraction(request, monkeypatch):
    if request.param == "no_filter":
        monkeypatch.delattr(tarfile, "tar_filter", raising=False)
    elif not hasattr(tarfile, "tar_filter"):
        pytest.skip("this Python has no tarfile extraction filters")
    return request.param


def _tar_with(members):
    """A gzip tar of (TarInfo, payload-or-None) pairs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, payload in members:
            if payload is not None:
                info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload) if payload is not None else None)
    return buf.getvalue()


def _symlink(name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


@pytest.mark.parametrize("name", ["../../escaped.txt", "rundir/../../../escaped.txt"])
def test_dotdot_members_never_leave_dest(tmp_path, extraction, name):
    dest = tmp_path / "a" / "dest"
    dest.mkdir(parents=True)
    with pytest.raises(Exception):
        untar_to_directory(_tar_of(["rundir/ok.txt", name]), dest)
    assert not list(tmp_path.rglob("escaped.txt"))
    assert list(dest.iterdir()) == []


def test_writing_through_an_escaping_symlink_is_refused(tmp_path, extraction):
    dest = tmp_path / "dest"
    outside = tmp_path / "outside"
    outside.mkdir()
    blob = _tar_with([(_symlink("rundir/out", str(outside)), None),
                      (tarfile.TarInfo("rundir/out/pwned.txt"), b"x")])
    with pytest.raises(Exception):
        untar_to_directory(blob, dest)
    assert not (outside / "pwned.txt").exists()


def test_a_hard_link_to_a_file_outside_is_refused(tmp_path, extraction):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret\n")
    dest = tmp_path / "dest"
    link = tarfile.TarInfo("rundir/stolen")
    link.type = tarfile.LNKTYPE
    link.linkname = "../../secret.txt"
    with pytest.raises(Exception):
        untar_to_directory(_tar_with([(link, None)]), dest)
    assert not (dest / "rundir" / "stolen").exists()


def test_setuid_bits_are_cleared(tmp_path, extraction):
    info = tarfile.TarInfo("rundir/tool")
    info.mode = 0o4777
    untar_to_directory(_tar_with([(info, b"#!/bin/sh\n")]), tmp_path / "dest")
    mode = os.stat(tmp_path / "dest" / "rundir" / "tool").st_mode & 0o7777
    assert mode & 0o7000 == 0 and mode & 0o022 == 0


def test_absolute_symlinks_in_a_rundir_still_restore(tmp_path, extraction):
    producer = tmp_path / "producer"
    target = producer / "par-rundir" / "pre_place"
    blob = tar_directory(_rundir(producer, target), arcname="par-rundir")
    consumer = tmp_path / "consumer"
    untar_to_directory(blob, consumer)
    untar_to_directory(blob, consumer)
    assert os.readlink(consumer / "par-rundir" / "post_place") == str(target)
    assert (consumer / "par-rundir" / "pre_place" / "db.txt").read_text() == "checkpoint\n"


def test_an_absolute_hard_link_is_refused(tmp_path, extraction):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret\n")
    link = tarfile.TarInfo("rundir/stolen")
    link.type = tarfile.LNKTYPE
    link.linkname = str(secret)
    with pytest.raises(Exception):
        untar_to_directory(_tar_with([(link, None)]), tmp_path / "dest")
    assert not (tmp_path / "dest" / "rundir" / "stolen").exists()


def test_hard_links_inside_the_archive_still_restore(tmp_path, extraction):
    src = tmp_path / "src" / "rundir"
    src.mkdir(parents=True)
    (src / "a.txt").write_text("same\n")
    os.link(src / "a.txt", src / "b.txt")
    blob = tar_directory(src, arcname="rundir")
    untar_to_directory(blob, tmp_path / "dest")
    assert (tmp_path / "dest" / "rundir" / "b.txt").read_text() == "same\n"
