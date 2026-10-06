"""The in-run checkpoint streamer pushes each new checkpoint once.

Comparing against the step push_checkpoint_db returned (clamped to the resume
ceiling, or None when the push is refused) re-uploaded the same checkpoint
every interval for the rest of the run.
"""
import threading
import time

import pytest

from hammer.vlsi import pd_cache, substep_resume


@pytest.mark.parametrize("push_result", ["write_regs", None], ids=["clamped", "refused"])
def test_one_push_per_new_checkpoint(monkeypatch, tmp_path, push_result):
    monkeypatch.setenv("HAMMER_CHECKPOINT_STREAM_SECS", "0.01")
    monkeypatch.setattr(substep_resume, "is_enabled", lambda d: True)
    monkeypatch.setattr(substep_resume, "_db_enabled", lambda d: True)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda d, s: "Top", raising=False)
    monkeypatch.setattr(pd_cache, "_live_tool_cpu_seconds", lambda: None)
    seen = ["syn_generic", "syn_map", "write_regs", "syn_opt"]  # past the ceiling
    monkeypatch.setattr(substep_resume, "confirmed_checkpoints", lambda r, l: seen)
    pushes = []

    def push(*a, **k):
        pushes.append(time.monotonic())
        return push_result

    monkeypatch.setattr(substep_resume, "push_checkpoint_db", push)
    done = threading.Event()

    def run():
        done.wait(0.3)  # ~30 streamer intervals
        return "ok"

    assert pd_cache._run_with_checkpoint_stream(object(), "synthesis", str(tmp_path), run) == "ok"
    assert len(pushes) == 1

    seen.append("syn_opt_2")
    pd_cache._run_with_checkpoint_stream(object(), "synthesis", str(tmp_path), lambda: done.wait(0.1))
    assert len(pushes) == 2
