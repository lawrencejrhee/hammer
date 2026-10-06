"""A restore lands at the rundir it was asked for and nowhere else.

Stage blobs and checkpoints come from a table every group member can write,
so neither the archive's top-level name nor a checkpoint's step may choose
where data lands.
"""
import gzip
import io
import tarfile

import pytest

from hammer.vlsi import pd_store
from hammer.vlsi.pd_store import materialize_checkpoint, tar_directory, untar_to_directory


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


@pytest.mark.parametrize("step", ["place/../../../victim", "../x", "", "a b", None])
def test_a_hostile_checkpoint_step_is_refused(tmp_path, step):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep\n")
    rundir = tmp_path / "obj" / "par-rundir"
    (rundir / "pre_place").mkdir(parents=True)
    rec = {"step": step, "is_dir": False, "data": gzip.compress(b"pwned")}
    with pytest.raises(ValueError, match="step name"):
        materialize_checkpoint(rec, rundir)
    assert (victim / "keep.txt").read_text() == "keep\n"


def test_a_directory_checkpoint_writes_only_its_pre_step(tmp_path):
    src = tmp_path / "src"
    (src / "pre_place_opt").mkdir(parents=True)
    (src / "pre_place_opt" / "db").write_text("ckpt\n")
    rundir = _rundir(tmp_path, "par-rundir", "keep\n")
    data = tar_directory(src / "pre_place_opt", arcname="syn-output.json")  # lies about its name
    dest = materialize_checkpoint({"step": "place_opt", "is_dir": True, "data": data}, rundir)
    assert dest == rundir / "pre_place_opt"
    assert (dest / "db").read_text() == "ckpt\n"
    assert (rundir / "syn-output.json").read_text() == "keep\n"


@pytest.mark.parametrize("sha", ["", "%", "ab_", "zz"])
def test_a_non_hex_sha_filter_is_refused(sha):
    with pytest.raises(ValueError):
        pd_store._blob_filter_sql(sha=sha)


def test_a_sha_prefix_filter_matches_by_prefix_only():
    where, params, n = pd_store._blob_filter_sql(sha="AbC1")
    assert n == 1 and "LIKE" not in where
    assert params == [4, "abc1"]
