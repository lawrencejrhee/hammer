import os

from hammer.vlsi.substep_resume import confirmed_checkpoints


def _innovus_rundir(tmp_path, writes, latest):
    for i, step in enumerate(writes):
        ck = tmp_path / f"pre_{step}"
        ck.mkdir(exist_ok=True)
        (ck / "db").write_text(str(i))
        os.utime(ck, (1000 + i, 1000 + i))
    (tmp_path / "innovus.log").write_text(
        "".join(f"Writing Binary DB to pre_{s}/ in single-threaded mode...\n" for s in writes))
    os.symlink(f"pre_{latest}", tmp_path / "latest")
    return str(tmp_path)


class TestRemovedSteps:
    def test_removed_steps_are_never_resume_points(self, tmp_path) -> None:
        rundir = _innovus_rundir(tmp_path, ["a", "dummy_step", "b", "dummy_step", "c"], latest="c")
        assert confirmed_checkpoints(rundir, "innovus.log") == ["a", "b", "c"]

    def test_completed_step_before_a_repeated_removal_is_kept(self, tmp_path) -> None:
        rundir = _innovus_rundir(tmp_path, ["a", "dummy_step", "b", "dummy_step"], latest="dummy_step")
        assert confirmed_checkpoints(rundir, "innovus.log") == ["a", "b"]

    def test_unfinished_write_over_a_removal_checkpoint_is_not_trusted(self, tmp_path) -> None:
        rundir = _innovus_rundir(tmp_path, ["a", "dummy_step", "b", "dummy_step"], latest="b")
        assert confirmed_checkpoints(rundir, "innovus.log") == ["a", "b"]

    def test_genus_removed_step(self, tmp_path) -> None:
        for i, step in enumerate(["a", "dummy_step", "b"]):
            (tmp_path / f"pre_{step}").write_text("db")
            os.utime(tmp_path / f"pre_{step}", (1000 + i, 1000 + i))
        (tmp_path / "genus.log").write_text("".join(
            f"Finished exporting design database to file 'pre_{s}'\n" for s in ["a", "dummy_step", "b"]))
        assert confirmed_checkpoints(str(tmp_path), "genus.log") == ["a", "b"]
