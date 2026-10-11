"""A cache restore into a rundir named differently from the one the blob was
stored from points its outputs at the new rundir, or runs the stage when it
cannot, with or without tarfile's extraction filters."""
import json
import os
import sys
import tarfile
from types import SimpleNamespace

import pytest

from hammer.vlsi import pd_cache, pd_store

RTL = "/src/ChipTop.sv"


def _outputs(obj, name):
    return {
        "synthesis.outputs.output_files": [f"{obj}/{name}/ChipTop.mapped.v"],
        "synthesis.outputs.sdc": f"{obj}/{name}/ChipTop.mapped.sdc",
        "synthesis.inputs.input_files": [RTL],
        "vlsi.inputs.sram_parameters": f"{obj}/sram_generator-output.json",
        "vlsi.builtins.is_complete": False,
    }


def _blob(producer_obj, name="syn-rundir", outputs=None, mode=None):
    rundir = producer_obj / name
    rundir.mkdir(parents=True)
    (rundir / "ChipTop.mapped.v").write_text("module ChipTop; endmodule\n")
    out = rundir / "syn-output.json"
    out.write_text(json.dumps(outputs or _outputs(producer_obj, name), indent=4))
    if mode is not None:
        out.chmod(mode)
    return pd_store.tar_directory(rundir)


@pytest.fixture(params=["tar_filter", "no_tar_filter"])
def cache(request, monkeypatch):
    if request.param == "no_tar_filter":
        monkeypatch.delattr(tarfile, "tar_filter", raising=False)
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda driver: None)
    monkeypatch.setattr(pd_cache, "_stage_module", lambda driver, stage: None)
    monkeypatch.setattr(pd_cache, "_build_cache_key", lambda driver, stage: "k" * 64)
    monkeypatch.setattr(pd_cache, "_run_with_checkpoint_stream", lambda driver, tag, rundir, fn: fn())
    monkeypatch.setattr(pd_store, "store_stage_blob", lambda *a, **k: None)
    state = SimpleNamespace(blob=None, events=[], warnings=[], runs=[])
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda tag, kind, **k: state.events.append(kind))
    monkeypatch.setattr(pd_store, "load_stage_blob", lambda key: ("synthesis", state.blob, 100.0, None))
    state.driver = SimpleNamespace(log=SimpleNamespace(info=lambda msg: None, warning=state.warnings.append))
    return state


def _cache_or_run(cache, rundir):
    return pd_cache.cache_or_run(cache.driver, "synthesis", str(rundir), "syn-output.json",
                                 run_fn=lambda: cache.runs.append(1) or (True, {"ran": "locally"}))


def test_a_hit_under_a_new_name_points_into_the_new_rundir(tmp_path, cache):
    a, b = tmp_path / "A" / "build", tmp_path / "B" / "build"
    cache.blob = _blob(a)
    rundir = b / "syn-ChipTop"

    ok, output = _cache_or_run(cache, rundir)

    assert ok and cache.events == ["HIT"] and cache.runs == []
    assert output == _outputs(b, "syn-ChipTop")
    assert json.loads((rundir / "syn-output.json").read_text()) == output
    assert not (b / "syn-rundir").exists()


def test_a_rename_inside_the_same_obj_dir_points_at_the_new_rundir(tmp_path, cache):
    obj = tmp_path / "build"
    cache.blob = _blob(obj)
    rundir = obj / "syn-ChipTop"

    ok, output = _cache_or_run(cache, rundir)

    assert ok and cache.events == ["HIT"]
    assert output == _outputs(obj, "syn-ChipTop")
    assert (obj / "syn-rundir" / "syn-output.json").exists()


def test_a_same_name_restore_rewrites_only_the_obj_dir(tmp_path, cache):
    a, b = tmp_path / "A" / "build", tmp_path / "B" / "build"
    outputs = dict(_outputs(a, "syn-rundir"), extra=[f"{a}/syn-rundir", f"{a}-iso/syn-rundir/x.v"])
    cache.blob = _blob(a, outputs=outputs)
    rundir = b / "syn-rundir"

    _cache_or_run(cache, rundir)

    expected = json.dumps(dict(_outputs(b, "syn-rundir"), extra=[f"{b}/syn-rundir", f"{a}-iso/syn-rundir/x.v"]),
                          indent=4)
    assert (rundir / "syn-output.json").read_text() == expected
    assert cache.events == ["HIT"]


def test_the_skip_path_restore_under_a_new_name_points_into_the_new_rundir(tmp_path, cache):
    a, b = tmp_path / "A" / "build", tmp_path / "B" / "build"
    cache.blob = _blob(a)
    rundir = b / "syn-ChipTop"

    assert pd_cache.try_restore_from_cache(cache.driver, "synthesis", str(rundir), "syn-output.json") is True

    assert json.loads((rundir / "syn-output.json").read_text()) == _outputs(b, "syn-ChipTop")
    assert cache.events == ["SKIP_RESTORED"]


def _unrebaseable(tmp_path):
    a = tmp_path / "A" / "build"
    return _blob(a, name="latest-syn", outputs=_outputs(a, "syn-rundir"))


def test_a_rename_with_no_path_under_the_stored_rundir_runs_the_stage(tmp_path, cache):
    cache.blob = _unrebaseable(tmp_path)

    result = _cache_or_run(cache, tmp_path / "B" / "build" / "syn-ChipTop")

    assert result == (True, {"ran": "locally"})
    assert cache.runs == [1] and cache.events == ["MISS_STORE"]
    assert any("hit but restore failed" in w and "latest-syn" in w for w in cache.warnings)


def test_the_skip_path_leaves_no_output_when_it_cannot_rebase(tmp_path, cache):
    cache.blob = _unrebaseable(tmp_path)
    rundir = tmp_path / "B" / "build" / "syn-ChipTop"

    assert pd_cache.try_restore_from_cache(cache.driver, "synthesis", str(rundir), "syn-output.json") is False

    assert not (rundir / "syn-output.json").exists()
    assert cache.events == []
    assert any("hit but restore failed" in w for w in cache.warnings)


def _symlinked_blob(tmp_path, link_name, absolute):
    a = tmp_path / "A" / "build"
    victims = {}
    for obj in (a, tmp_path / "B" / "build"):
        out = obj / "real-syn" / "syn-output.json"
        out.parent.mkdir(parents=True)
        out.write_text(json.dumps(_outputs(a, "syn-rundir"), indent=4))
        victims[out] = out.read_text()
    link = a / link_name
    link.symlink_to(a / "real-syn" if absolute else "real-syn")
    return pd_store.tar_directory(link), victims


@pytest.mark.parametrize("link_name", ["latest-syn", "syn-rundir"])
@pytest.mark.parametrize("absolute", [True, False])
def test_a_symlinked_blob_under_a_new_name_runs_the_stage_in_a_real_rundir(tmp_path, cache, link_name, absolute):
    cache.blob, victims = _symlinked_blob(tmp_path, link_name, absolute)
    rundir = tmp_path / "B" / "build" / "syn-ChipTop"

    result = _cache_or_run(cache, rundir)

    assert result == (True, {"ran": "locally"})
    assert cache.runs == [1] and cache.events == ["MISS_STORE"]
    assert rundir.is_dir() and not rundir.is_symlink()
    assert {p: p.read_text() for p in victims} == victims


@pytest.mark.parametrize("link_name", ["latest-syn", "syn-rundir"])
@pytest.mark.parametrize("absolute", [True, False])
def test_the_skip_path_drops_a_symlinked_blob_under_a_new_name(tmp_path, cache, link_name, absolute):
    cache.blob, victims = _symlinked_blob(tmp_path, link_name, absolute)
    rundir = tmp_path / "B" / "build" / "syn-ChipTop"

    assert pd_cache.try_restore_from_cache(cache.driver, "synthesis", str(rundir), "syn-output.json") is False

    assert not os.path.lexists(rundir)
    assert {p: p.read_text() for p in victims} == victims


def _blob_with_a_symlinked_json(tmp_path, json_name, absolute):
    a = tmp_path / "A" / "build"
    rundir = a / "syn-rundir"
    rundir.mkdir(parents=True)
    victim = tmp_path / "outside.json"
    victim.write_text(json.dumps(_outputs(a, "syn-rundir"), indent=4))
    if json_name != "syn-output.json":
        (rundir / "syn-output.json").write_text(victim.read_text())
    (rundir / json_name).symlink_to(victim if absolute else "../../../outside.json")
    return pd_store.tar_directory(rundir), {victim: victim.read_text()}


@pytest.mark.parametrize("json_name", ["syn-output.json", "extra.json"])
@pytest.mark.parametrize("absolute", [True, False])
def test_a_rename_holding_a_symlinked_json_runs_the_stage_and_writes_nothing_outside(tmp_path, cache, json_name,
                                                                                    absolute):
    cache.blob, victims = _blob_with_a_symlinked_json(tmp_path, json_name, absolute)

    result = _cache_or_run(cache, tmp_path / "B" / "build" / "syn-ChipTop")

    assert result == (True, {"ran": "locally"})
    assert cache.runs == [1] and cache.events == ["MISS_STORE"]
    assert {p: p.read_text() for p in victims} == victims


@pytest.mark.parametrize("json_name", ["syn-output.json", "extra.json"])
@pytest.mark.parametrize("absolute", [True, False])
def test_the_skip_path_drops_a_rename_holding_a_symlinked_json(tmp_path, cache, json_name, absolute):
    cache.blob, victims = _blob_with_a_symlinked_json(tmp_path, json_name, absolute)
    rundir = tmp_path / "B" / "build" / "syn-ChipTop"

    assert pd_cache.try_restore_from_cache(cache.driver, "synthesis", str(rundir), "syn-output.json") is False

    assert not os.path.lexists(rundir / "syn-output.json")
    assert {p: p.read_text() for p in victims} == victims


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs POSIX permissions and a non-root user")
def test_a_restore_whose_paths_cannot_be_rewritten_runs_the_stage(tmp_path, cache):
    cache.blob = _blob(tmp_path / "A" / "build", mode=0o444)

    result = _cache_or_run(cache, tmp_path / "B" / "build" / "syn-rundir")

    assert result == (True, {"ran": "locally"})
    assert cache.runs == [1] and cache.events == ["MISS_STORE"]
    assert any("hit but restore failed" in w for w in cache.warnings)
