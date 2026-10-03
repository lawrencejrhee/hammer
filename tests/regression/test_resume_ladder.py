import json
import os

import pytest

from hammer.vlsi import pd_store, substep_resume
from hammer.vlsi.substep_resume import MARKER_NAME, plan_resume


def _attempt(rundir, log, steps, t):
    """One genus attempt: writes and confirms pre_<step> for each step from time t, then its log."""
    for i, step in enumerate(steps):
        (rundir / f"pre_{step}").write_text(f"db {step} {t}")
        os.utime(rundir / f"pre_{step}", (t + i, t + i))
    (rundir / log).write_text("".join(
        f"Finished exporting design database to file 'pre_{s}'\n" for s in steps))
    os.utime(rundir / log, (t + len(steps), t + len(steps)))


def _start(rundir, resumed_from, t, burned=None):
    """Stamp the marker as record_attempt does just before the tool runs."""
    old = json.loads((rundir / MARKER_NAME).read_text()) if (rundir / MARKER_NAME).exists() else {}
    burned = old.get("burned", []) if burned is None else burned
    (rundir / MARKER_NAME).write_text(json.dumps(
        {"stage_key": "K", "resumed_from": resumed_from, "burned": burned}))
    os.utime(rundir / MARKER_NAME, (t, t))


class _FakeDb:
    """The checkpoint table: (key, step) rows, newest last."""

    def __init__(self, monkeypatch, rows=()):
        self.rows = list(rows)
        self.deleted, self.materialized, self.fetches = [], [], 0
        monkeypatch.setattr(pd_store, "fetch_checkpoint", self.fetch)
        monkeypatch.setattr(pd_store, "delete_checkpoints", self.delete)
        monkeypatch.setattr(pd_store, "materialize_checkpoint", self.materialize)

    def fetch(self, stage_key=None, step=None, ckpt_id=None):
        self.fetches += 1
        hits = [r for r in self.rows if r[0] == stage_key and step in (None, r[1])]
        return {"step": hits[-1][1], "size_bytes": 1} if hits else None

    def delete(self, stage_key=None, step=None, **kw):
        gone = [r for r in self.rows if r[0] == stage_key and step in (None, r[1])]
        self.rows = [r for r in self.rows if r not in gone]
        self.deleted += gone
        return len(gone)

    def materialize(self, rec, rundir):
        self.materialized.append(rec["step"])
        (rundir / f"pre_{rec['step']}").write_text("from db")


@pytest.fixture
def resume_env(monkeypatch):
    monkeypatch.setattr(substep_resume, "_stage_key", lambda driver, tag: "K")
    monkeypatch.delenv("HAMMER_SUBSTEP_RESUME", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


def _plan(rundir):
    plan = plan_resume(None, "synthesis", str(rundir), "syn-output.json", "genus.log")
    return plan["step"] if plan else None


class TestResumeLadder:
    def test_corrupt_lower_checkpoint_is_burned(self, tmp_path, resume_env) -> None:
        _start(tmp_path, None, 900)
        _attempt(tmp_path, "genus.log", ["a", "b", "c"], 1000)
        planned = []
        for i in range(4):
            step = _plan(tmp_path)
            planned.append(step)
            if step is None:
                break
            _start(tmp_path, step, 2000 + 1000 * i)
            (tmp_path / f"genus.log{i + 1}").write_text("ERROR: failed to load checkpoint\n")
            os.utime(tmp_path / f"genus.log{i + 1}", (2500 + 1000 * i,) * 2)
        assert planned == ["c", "b", "a", None]

    def test_a_resume_that_made_progress_keeps_its_rung(self, tmp_path, resume_env) -> None:
        _start(tmp_path, None, 900)
        _attempt(tmp_path, "genus.log", ["a", "b", "c"], 1000)
        _start(tmp_path, "b", 2000, burned=["c"])
        _attempt(tmp_path, "genus.log1", ["c"], 2100)
        assert _plan(tmp_path) == "b"
        assert json.loads((tmp_path / MARKER_NAME).read_text())["burned"] == ["c"]


class TestExhaustedLadder:
    def _burned_rundir(self, tmp_path):
        _start(tmp_path, None, 900)
        _attempt(tmp_path, "genus.log", ["x"], 1000)
        _start(tmp_path, "x", 2000)
        return tmp_path

    def test_burned_db_checkpoint_is_not_fetched_again(self, tmp_path, resume_env, monkeypatch) -> None:
        monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "1")
        db = _FakeDb(monkeypatch, [("K", "x")])
        assert _plan(self._burned_rundir(tmp_path)) is None
        assert db.deleted == [("K", "x")] and db.materialized == []

    def test_unburned_db_checkpoint_is_still_offered(self, tmp_path, resume_env, monkeypatch) -> None:
        monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "1")
        db = _FakeDb(monkeypatch, [("K", "y"), ("K", "x"), ("other", "x")])
        plan = plan_resume(None, "synthesis", str(self._burned_rundir(tmp_path)), "syn-output.json", "genus.log")
        assert plan is not None and plan["step"] == "y" and plan["source"] == "database"
        assert db.rows == [("K", "y"), ("other", "x")]

    def test_db_checkpoints_off_makes_no_db_call(self, tmp_path, resume_env, monkeypatch) -> None:
        db = _FakeDb(monkeypatch, [("K", "x")])
        assert _plan(self._burned_rundir(tmp_path)) is None
        assert db.fetches == 0 and db.deleted == []

    def test_no_loop_across_reruns(self, tmp_path, resume_env, monkeypatch) -> None:
        monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "1")
        db = _FakeDb(monkeypatch)
        _start(tmp_path, None, 900)
        _attempt(tmp_path, "genus.log", ["x"], 1000)
        db.rows.append(("K", "x"))
        planned = []
        for i in range(4):
            step = _plan(tmp_path)
            planned.append(step)
            _start(tmp_path, step, 2000 + 1000 * i)
            if step is None:
                _attempt(tmp_path, f"genus.log{i + 1}", ["x"], 2100 + 1000 * i)
            else:
                (tmp_path / f"genus.log{i + 1}").write_text("ERROR: failed to load checkpoint\n")
                os.utime(tmp_path / f"genus.log{i + 1}", (2500 + 1000 * i,) * 2)
            db.rows.append(("K", "x"))
        assert planned == ["x", None, "x", None]
        assert db.materialized == []
