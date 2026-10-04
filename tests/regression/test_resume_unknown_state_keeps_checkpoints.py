import json
import os

import pytest

from hammer.vlsi import substep_resume
from hammer.vlsi.substep_resume import MARKER_NAME, plan_resume, record_attempt


def _crashed_attempt(rundir, steps=("a", "b")):
    for i, step in enumerate(steps):
        (rundir / f"pre_{step}").write_text(f"db {step}")
        os.utime(rundir / f"pre_{step}", (1000 + i, 1000 + i))
    (rundir / "genus.log").write_text("".join(
        f"Finished exporting design database to file 'pre_{s}'\n" for s in steps))
    os.utime(rundir / "genus.log", (1100, 1100))


def _present(rundir):
    return sorted(p.name for p in rundir.glob("pre_*"))


@pytest.fixture
def key(monkeypatch):
    current = {"key": "K"}
    monkeypatch.setattr(substep_resume, "_stage_key", lambda driver, tag: current["key"])
    monkeypatch.delenv("HAMMER_SUBSTEP_RESUME", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    return current


def _plan(rundir):
    return plan_resume(None, "synthesis", str(rundir), "syn-output.json", "genus.log")


def test_unknown_key_keeps_checkpoints_and_marker(tmp_path, key) -> None:
    _crashed_attempt(tmp_path)
    (tmp_path / MARKER_NAME).write_text(json.dumps({"stage_key": "K"}))
    key["key"] = None
    assert _plan(tmp_path) is None
    assert _present(tmp_path) == ["pre_a", "pre_b"]
    assert json.loads((tmp_path / MARKER_NAME).read_text()) == {"stage_key": "K"}


def test_unknown_key_keeps_checkpoints_of_a_completed_run(tmp_path, key) -> None:
    _crashed_attempt(tmp_path)
    (tmp_path / MARKER_NAME).write_text(json.dumps({"stage_key": "old"}))
    (tmp_path / "syn-output.json").write_text("{}")
    key["key"] = None
    assert _plan(tmp_path) is None
    assert _present(tmp_path) == ["pre_a", "pre_b"]


def test_unreadable_marker_keeps_checkpoints(tmp_path, key) -> None:
    _crashed_attempt(tmp_path)
    (tmp_path / MARKER_NAME).mkdir()
    assert _plan(tmp_path) is None
    record_attempt(None, "synthesis", str(tmp_path), None)
    assert _present(tmp_path) == ["pre_a", "pre_b"]
    assert (tmp_path / MARKER_NAME).is_dir()


def test_unknown_key_records_nothing(tmp_path, key) -> None:
    _crashed_attempt(tmp_path)
    (tmp_path / MARKER_NAME).write_text(json.dumps({"stage_key": "old"}))
    key["key"] = None
    record_attempt(None, "synthesis", str(tmp_path), None)
    assert _present(tmp_path) == ["pre_a", "pre_b"]
    assert json.loads((tmp_path / MARKER_NAME).read_text()) == {"stage_key": "old"}


def test_known_key_with_a_matching_marker_still_resumes(tmp_path, key) -> None:
    _crashed_attempt(tmp_path)
    (tmp_path / MARKER_NAME).write_text(json.dumps({"stage_key": "K"}))
    assert _plan(tmp_path)["step"] == "b"
