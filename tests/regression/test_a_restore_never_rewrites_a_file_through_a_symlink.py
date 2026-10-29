"""A cache restore that points a rundir's paths at its new location never writes through a symlink in the blob:
not a top-level json, the enter file, a generated script, a symlinked generated-scripts dir, a link inside a
symlinked dir or the rundir itself."""
import json
import os
from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_run_with_checkpoint_stream", lambda driver, tag, rundir, fn: fn())
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: None)
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda *a, **k: None)
    state = SimpleNamespace(blob=None, runs=[])
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: ("par", state.blob, 100.0, None))
    state.driver = SimpleNamespace(log=SimpleNamespace(info=lambda msg: None, warning=lambda msg: None))
    return state


@pytest.mark.parametrize("link", ["extra.json", "enter", "generated-scripts/open_chip", "generated-scripts"])
def test_a_symlinked_entry_keeps_its_target_untouched(tmp_path, cache, link):
    work_a = tmp_path / "A"
    rundir_a = work_a / "build" / "par-rundir"
    (rundir_a / "generated-scripts").mkdir(parents=True)
    (rundir_a / "par-output.json").write_text(json.dumps({"par.outputs.output_gds": f"{rundir_a}/ChipTop.gds"}))
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "open_chip" if link == "generated-scripts" else outside / "victim"
    victim.write_text(f'{{"x": "{rundir_a}/x"}}\ncd {rundir_a}\n')
    if link == "generated-scripts":
        (rundir_a / "generated-scripts").rmdir()
        (rundir_a / link).symlink_to(outside)
    else:
        (rundir_a / link).symlink_to(victim)
    cache.blob = pd_store.tar_directory(rundir_a)
    before = victim.read_text()

    ok, output = pd_cache.cache_or_run(cache.driver, "par", str(tmp_path / "B" / "build" / "par-rundir"),
                                       "par-output.json", run_fn=lambda: cache.runs.append(1) or (True, {}))

    assert ok and cache.runs == []
    assert output == {"par.outputs.output_gds": f"{tmp_path / 'B' / 'build' / 'par-rundir'}/ChipTop.gds"}
    assert victim.read_text() == before


def test_a_link_inside_a_symlinked_dir_keeps_its_target(tmp_path, cache):
    rundir_a = tmp_path / "A" / "build" / "par-rundir"
    (rundir_a / "pre_route" / "libs").mkdir(parents=True)
    (rundir_a / "par-output.json").write_text(json.dumps({"par.outputs.output_gds": f"{rundir_a}/ChipTop.gds"}))
    (rundir_a / "pre_route" / "libs" / "tech.lef").symlink_to(rundir_a.parent / "tech-cache" / "tech.lef")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tech.lef").symlink_to(rundir_a.parent / "tech-cache" / "tech.lef")
    (rundir_a / "pre_route" / "outside").symlink_to(outside)
    cache.blob = pd_store.tar_directory(rundir_a)
    rundir_b = tmp_path / "B" / "build" / "par-rundir"

    ok, _ = pd_cache.cache_or_run(cache.driver, "par", str(rundir_b), "par-output.json",
                                  run_fn=lambda: cache.runs.append(1) or (True, {}))

    assert ok and cache.runs == []
    assert os.readlink(rundir_b / "pre_route" / "libs" / "tech.lef") == str(rundir_b.parent / "tech-cache" / "tech.lef")
    assert os.readlink(rundir_b / "pre_route" / "outside") == str(outside)
    assert os.readlink(outside / "tech.lef") == str(rundir_a.parent / "tech-cache" / "tech.lef")


def test_a_symlinked_rundir_keeps_its_target_untouched(tmp_path, cache):
    outside = tmp_path / "scratch" / "par"
    (outside / "generated-scripts").mkdir(parents=True)
    rundir_a = tmp_path / "A" / "build" / "par-rundir"
    rundir_a.parent.mkdir(parents=True)
    rundir_a.symlink_to(outside)
    (outside / "par-output.json").write_text(json.dumps({"par.outputs.output_gds": f"{rundir_a}/ChipTop.gds"}))
    (outside / "generated-scripts" / "open_chip").write_text(f"cd {rundir_a}\n")
    (outside / "enter").write_text(f"export PAR_DIR={rundir_a}\n")
    cache.blob = pd_store.tar_directory(rundir_a)
    before = {p: p.read_text() for p in outside.rglob("*") if p.is_file()}

    pd_cache.cache_or_run(cache.driver, "par", str(tmp_path / "B" / "build" / "par-rundir"), "par-output.json",
                          run_fn=lambda: cache.runs.append(1) or (True, {}))

    assert {p: p.read_text() for p in outside.rglob("*") if p.is_file()} == before
