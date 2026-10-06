"""A restore lands at the rundir it was asked for and nowhere else.

Stage blobs come from a table every group member can write, so the
archive's top-level name may not choose where data lands.
"""
import io
import tarfile

import pytest

from hammer.vlsi.pd_store import tar_directory, untar_to_directory


def _rundir(root, name="syn-rundir", text="fresh\n"):
    rundir = root / name
    rundir.mkdir(parents=True)
    (rundir / "syn-output.json").write_text(text)
    return rundir


def test_a_blob_restores_under_the_requested_name(tmp_path):
    blob = tar_directory(_rundir(tmp_path / "producer"), arcname="syn-rundir")
    obj = tmp_path / "obj"
    sibling = _rundir(obj, "syn-rundir", "someone else's run\n")

    untar_to_directory(blob, obj, as_name="my-syn")

    assert (obj / "my-syn" / "syn-output.json").read_text() == "fresh\n"
    assert (sibling / "syn-output.json").read_text() == "someone else's run\n"
    assert sorted(p.name for p in obj.iterdir()) == ["my-syn", "syn-rundir"]


def test_a_blob_with_two_top_level_entries_is_refused(tmp_path):
    src = tmp_path / "src"
    _rundir(src, "syn-rundir")
    (src / "extra.txt").write_text("x\n")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(src / "syn-rundir"), arcname="syn-rundir")
        tar.add(str(src / "extra.txt"), arcname="extra.txt")
    dest = tmp_path / "dest"
    with pytest.raises(ValueError, match="one top-level entry"):
        untar_to_directory(buf.getvalue(), dest, as_name="syn-rundir")
    assert list(dest.iterdir()) == []
