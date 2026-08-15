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
