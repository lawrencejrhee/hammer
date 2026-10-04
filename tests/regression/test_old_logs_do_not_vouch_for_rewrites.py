import json
import os

from hammer.vlsi import substep_resume
from hammer.vlsi.substep_resume import MARKER_NAME, clean_checkpoints, confirmed_checkpoints, plan_resume


def _write(path, t, text="db"):
    path.write_text(text)
    os.utime(path, (t, t))


def _genus_log(rundir, name, steps, t):
    _write(rundir / name, t, "".join(
        f"Finished exporting design database to file 'pre_{s}'\n" for s in steps))


class TestGenus:
    def test_after_a_clean_only_new_logs_confirm(self, tmp_path) -> None:
        _write(tmp_path / "pre_a", 1000)
        _write(tmp_path / "pre_b", 1001)
        _genus_log(tmp_path, "genus.log", ["a", "b"], 1002)
        clean_checkpoints(str(tmp_path))
        _write(tmp_path / "pre_a", 2000)
        _genus_log(tmp_path, "genus.log1", ["a"], 2001)
        _write(tmp_path / "pre_b", 2002, "half written")
        assert confirmed_checkpoints(str(tmp_path), "genus.log") == ["a"]

    def test_a_later_attempt_rewriting_a_checkpoint_needs_its_own_confirmation(self, tmp_path) -> None:
        for i, step in enumerate(["a", "b", "c"]):
            _write(tmp_path / f"pre_{step}", 1000 + i)
        _genus_log(tmp_path, "genus.log", ["a", "b", "c"], 1003)
        _write(tmp_path / "pre_c", 2000, "half written")
        _write(tmp_path / "genus.log1", 2001, "resuming from b\n")
        assert confirmed_checkpoints(str(tmp_path), "genus.log") == ["a", "b"]
        _write(tmp_path / "pre_c", 3000, "rewritten")
        _genus_log(tmp_path, "genus.log2", ["c"], 3001)
        assert confirmed_checkpoints(str(tmp_path), "genus.log") == ["a", "b", "c"]

    def test_resume_after_a_clean_skips_the_half_written_checkpoint(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(substep_resume, "_stage_key", lambda driver, tag: "K")
        monkeypatch.delenv("HAMMER_SUBSTEP_RESUME", raising=False)
        monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
        _write(tmp_path / "pre_a", 1000)
        _write(tmp_path / "pre_b", 1001)
        _genus_log(tmp_path, "genus.log", ["a", "b"], 1002)
        clean_checkpoints(str(tmp_path))
        _write(tmp_path / MARKER_NAME, 1500, json.dumps({"stage_key": "K"}))
        _write(tmp_path / "pre_a", 2000)
        _genus_log(tmp_path, "genus.log1", ["a"], 2001)
        _write(tmp_path / "pre_b", 2002, "half written")
        plan = plan_resume(None, "synthesis", str(tmp_path), "syn-output.json", "genus.log")
        assert plan is not None and plan["step"] == "a"


class TestInnovus:
    def _rundir(self, tmp_path, latest_t):
        for step in ["a", "b"]:
            ck = tmp_path / f"pre_{step}"
            ck.mkdir()
            (ck / "db").write_text(step)
        os.utime(tmp_path / "pre_a", (900, 900))
        os.utime(tmp_path / "pre_b", (1005, 1005))
        _write(tmp_path / "innovus.log", 1000, "".join(
            f"Writing Binary DB to pre_{s}/ in single-threaded mode...\n" for s in ["a", "b"]))
        os.symlink("pre_b", tmp_path / "latest")
        os.utime(tmp_path / "latest", (latest_t, latest_t), follow_symlinks=False)
        return str(tmp_path)

    def test_latest_link_vouches_for_the_last_write(self, tmp_path) -> None:
        assert confirmed_checkpoints(self._rundir(tmp_path, 1006), "innovus.log") == ["a", "b"]

    def test_last_write_newer_than_log_and_link_is_not_trusted(self, tmp_path) -> None:
        assert confirmed_checkpoints(self._rundir(tmp_path, 999), "innovus.log") == ["a"]
