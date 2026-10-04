import json
import os

import pytest

from hammer.vlsi import substep_resume
from hammer.vlsi.substep_resume import MARKER_NAME, plan_resume, record_attempt


def _crashed_attempt(rundir):
    for i, step in enumerate(["a", "b"]):
        (rundir / f"pre_{step}").write_text(f"db {step}")
        os.utime(rundir / f"pre_{step}", (1000 + i, 1000 + i))
    (rundir / "genus.log").write_text("".join(
        f"Finished exporting design database to file 'pre_{s}'\n" for s in ["a", "b"]))
    os.utime(rundir / "genus.log", (1100, 1100))
    (rundir / MARKER_NAME).write_text('{"stage_key": "K", "bur')


def _present(rundir):
    return sorted(p.name for p in rundir.glob("pre_*"))


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(substep_resume, "_stage_key", lambda driver, tag: "K")
    monkeypatch.delenv("HAMMER_SUBSTEP_RESUME", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


def _plan(rundir):
    return plan_resume(None, "synthesis", str(rundir), "syn-output.json", "genus.log")


def test_corrupt_marker_cleans_and_restarts(tmp_path, key) -> None:
    _crashed_attempt(tmp_path)
    assert _plan(tmp_path) is None
    assert _present(tmp_path) == []
    record_attempt(None, "synthesis", str(tmp_path), None)
    assert json.loads((tmp_path / MARKER_NAME).read_text())["stage_key"] == "K"


def test_corrupt_marker_still_resumes_from_the_database(tmp_path, key, monkeypatch) -> None:
    _crashed_attempt(tmp_path)

    def fetch(driver, tag, rundir, skip=()):
        (tmp_path / "pre_a").write_text("db a from the database")
        return {"step": "a", "saved_seconds": None, "key": "K", "source": "database"}

    monkeypatch.setattr(substep_resume, "_db_fallback_plan", fetch)
    assert _plan(tmp_path)["step"] == "a"
    record_attempt(None, "synthesis", str(tmp_path), "a")
    assert _present(tmp_path) == ["pre_a"]
    assert (tmp_path / "pre_a").read_text() == "db a from the database"
