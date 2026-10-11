"""The in-run checkpoint streamer retries a push that may pass later and uploads each checkpoint once."""
import os
import time
import types

import pytest

from hammer.vlsi import pd_cache, pd_store, substep_resume

GENUS = ["init_environment", "syn_generic", "syn_map", "add_tieoffs", "write_regs",
         "generate_reports", "write_outputs"]
STORE = pd_store.store_checkpoint


class DbError(Exception):
    pass


class OperationalError(DbError):
    pass


class InterfaceError(DbError):
    pass


class ProgrammingError(DbError):
    pass


class Run:
    """A genus run whose confirmed checkpoints grow as the test advances it, and the uploads it tried."""

    def __init__(self, monkeypatch, rundir):
        self.rundir = rundir
        self.confirmed = []
        self.scans = 0
        self.tries = []
        self.fail = lambda step: None
        monkeypatch.setenv("HAMMER_CHECKPOINT_STREAM_SECS", "0.01")
        monkeypatch.setattr(substep_resume, "is_enabled", lambda d: True)
        monkeypatch.setattr(substep_resume, "_db_enabled", lambda d: True)
        monkeypatch.setattr(substep_resume, "_stage_key", lambda d, s: "key")
        monkeypatch.setattr(substep_resume, "announced_order", lambda r, l: GENUS)
        monkeypatch.setattr(substep_resume, "confirmed_checkpoints", self.scan)
        monkeypatch.setattr(pd_cache, "_stage_module", lambda d, s: "Top")
        monkeypatch.setattr(pd_cache, "_live_tool_cpu_seconds", lambda: None)
        monkeypatch.setattr(pd_store, "store_checkpoint", self.store)
        monkeypatch.setattr(pd_store, "psycopg2", types.SimpleNamespace(
            OperationalError=OperationalError, InterfaceError=InterfaceError))
        substep_resume.write_marker(rundir, {"stage_key": "key"})

    def scan(self, rundir, log_name):
        self.scans += 1
        return list(self.confirmed)

    def store(self, key, stage, step, path, **kw):
        self.tries.append(step)
        self.fail(step)
        return 1

    def advance(self, steps):
        for step in steps:
            self.confirmed.append(step)
            seen = self.scans + 8
            deadline = time.monotonic() + 5
            while self.scans < seen and time.monotonic() < deadline:
                time.sleep(0.005)

    def stream(self, *steps):
        pd_cache._run_with_checkpoint_stream(object(), "synthesis", self.rundir,
                                             lambda: self.advance(steps))
        return self.tries


@pytest.fixture
def run(monkeypatch, tmp_path):
    return Run(monkeypatch, str(tmp_path))


@pytest.mark.parametrize("error", [
    OperationalError("server closed the connection unexpectedly"),
    InterfaceError("connection already closed"),
    OSError("Input/output error"),
], ids=["operational", "interface", "io"])
def test_failed_upload_is_retried_next_interval(run, error):
    def blip(step):
        if len(run.tries) == 1:
            raise error

    run.fail = blip
    assert run.stream("syn_generic") == ["syn_generic", "syn_generic"]


def test_cache_key_trouble_is_retried_next_interval(run, monkeypatch):
    keys = iter([None])
    monkeypatch.setattr(substep_resume, "_stage_key", lambda d, s: next(keys, "key"))
    assert run.stream("syn_generic") == ["syn_generic"]


def test_unreachable_database_is_retried_without_tarring(run, monkeypatch):
    os.makedirs(os.path.join(run.rundir, "pre_syn_generic"))
    tars, connects = [], []

    def connect():
        connects.append(1)
        raise OperationalError("could not connect to server: Connection refused")

    monkeypatch.setattr(pd_store, "store_checkpoint", STORE)
    monkeypatch.setattr(pd_store, "_connect", connect)
    monkeypatch.setattr(pd_store, "tar_directory", lambda path, arcname=None: tars.append(path) or b"tar")
    run.stream("syn_generic")
    assert tars == [] and len(connects) > 1


@pytest.mark.parametrize("error", [
    RuntimeError("checkpoint tarball is 5000 MB compressed, past the 4096 MB sanity ceiling"),
    ProgrammingError("permission denied for table pd_checkpoints"),
    MemoryError(),
], ids=["oversized", "privilege", "memory"])
def test_refused_upload_is_not_retried(run, error):
    def refuse(step):
        raise error

    run.fail = refuse
    assert run.stream("syn_map", "write_regs", "generate_reports", "write_outputs") == ["syn_map", "write_regs"]


def test_moved_stage_key_uploads_nothing_and_is_checked_once_per_checkpoint(run, monkeypatch):
    substep_resume.write_marker(run.rundir, {"stage_key": "an earlier key"})
    push = substep_resume.push_checkpoint_db
    results = []

    def counted(*a, **k):
        results.append(push(*a, **k))
        return results[-1]

    monkeypatch.setattr(substep_resume, "push_checkpoint_db", counted)
    assert run.stream("syn_generic", "syn_map") == []
    assert results == ["syn_generic", "syn_map"]


def test_checkpoints_confirmed_mid_run_upload_once_up_to_write_regs(run):
    assert run.stream(*GENUS) == GENUS[:GENUS.index("write_regs") + 1]
