import functools
import io
import json
import tarfile
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.sim.mocksim import MockSim
from hammer.vlsi import CLIDriver, pd_cache, pd_store

SIMV = """#!/bin/sh
echo run >> ../../trace
echo "*** PASSED ***" >&2
"""
SUITE = {"name": "asm", "tests": ["a"], "pass_regex": "PASSED"}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN", raising=False)
    blobs, compiles = {}, []

    def store(stage, key, data, **kwargs):
        blobs[key] = (stage, data, 1.0, 1.0)

    monkeypatch.setattr(pd_store, "load_stage_blob", blobs.get)
    monkeypatch.setattr(pd_store, "store_stage_blob", store)
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)
    real = MockSim.step1

    @functools.wraps(real)
    def counted(self):
        compiles.append(1)
        return real(self)

    monkeypatch.setattr(MockSim, "step1", counted)
    (tmp_path / "tb.v").write_text("module tb; endmodule\n")
    return blobs, compiles


def _write(tmp_path: Path, **extra) -> None:
    config = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.sim_tool": "hammer.sim.mocksim",
        "vlsi.inputs.hierarchical.config_source": "none",
        "sim.inputs.top_module": "tb",
        "sim.inputs.input_files": ["tb.v"],
        "sim.inputs.level": "rtl",
        "sim.inputs.options": [],
        "sim.inputs.execute_sim": False,
        "sim.submit.command": "local",
        "sim.mocksim.temp_folder": str(tmp_path),
        "sim.mocksim.simv_script": SIMV,
        "sim.tests.suites": [SUITE],
    }
    config.update(extra)
    (tmp_path / "config.json").write_text(json.dumps(config, cls=HammerJSONEncoder))


def _sim(tmp_path: Path, *flags: str) -> int:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["sim", *flags, "-o", str(tmp_path / "output.json"), "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt"),
                               "--sim_rundir", str(tmp_path / "obj" / "sim-rundir")])
    return cm.value.code


def _runs(tmp_path: Path) -> int:
    return len((tmp_path / "trace").read_text().splitlines())


def test_a_rerun_restores_the_simulator_and_still_runs_the_tests(tmp_path, cache) -> None:
    _, compiles = cache
    _write(tmp_path)
    assert _sim(tmp_path, "--sim_tests", "asm") == 0
    assert _sim(tmp_path, "--sim_tests", "asm") == 0
    assert len(compiles) == 1
    assert _runs(tmp_path) == 2


def test_force_compiles_again(tmp_path, cache) -> None:
    _, compiles = cache
    _write(tmp_path)
    assert _sim(tmp_path) == 0
    assert _sim(tmp_path, "--force") == 0
    assert len(compiles) == 2


def test_an_edit_to_a_testbench_named_by_a_relative_path_compiles_again(tmp_path, cache) -> None:
    _, compiles = cache
    _write(tmp_path)
    assert _sim(tmp_path) == 0
    (tmp_path / "tb.v").write_text("module tb; wire changed; endmodule\n")
    assert _sim(tmp_path) == 0
    assert len(compiles) == 2


def test_an_edit_to_a_file_in_an_incdir_compiles_again(tmp_path, cache) -> None:
    _, compiles = cache
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "defs.vh").write_text("`define A 1\n")
    _write(tmp_path, **{"sim.inputs.options": ["+incdir+inc"]})
    assert _sim(tmp_path) == 0
    (tmp_path / "inc" / "defs.vh").write_text("`define A 2\n")
    assert _sim(tmp_path) == 0
    assert len(compiles) == 2


def test_changing_only_the_test_list_keeps_the_simulator(tmp_path, cache) -> None:
    _, compiles = cache
    _write(tmp_path)
    assert _sim(tmp_path) == 0
    _write(tmp_path, **{"sim.tests.suites": [dict(SUITE, tests=["a", "b"])], "sim.tests.run_flags": ["-q"]})
    assert _sim(tmp_path) == 0
    assert len(compiles) == 1


def test_the_stored_simulator_holds_no_files_from_earlier_test_runs(tmp_path, cache) -> None:
    blobs, _ = cache
    _write(tmp_path)
    rundir = tmp_path / "obj" / "sim-rundir"
    (rundir / "tests").mkdir(parents=True)
    (rundir / "tests" / "old.out").write_text("old\n")
    (rundir / "wave.vpd").write_text("old\n")
    assert _sim(tmp_path) == 0
    (_, data, _, _), = blobs.values()
    names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
    assert any(n.endswith("/simv") for n in names)
    assert not any("old.out" in n or n.endswith(".vpd") for n in names)


@pytest.mark.parametrize("extra", [
    {"sim.inputs.execute_sim": True},
    {"sim.tests.suites": []},
    {"vlsi.core.build_system": "hammer"},
])
def test_a_run_that_is_not_compile_only_with_tests_is_never_cached(tmp_path, cache, extra) -> None:
    blobs, compiles = cache
    _write(tmp_path, **extra)
    assert _sim(tmp_path) == 0
    assert _sim(tmp_path) == 0
    assert blobs == {}
    assert len(compiles) == 2


def test_a_compile_that_left_no_simulator_is_not_stored(tmp_path, cache) -> None:
    blobs, _ = cache
    _write(tmp_path, **{"sim.mocksim.simv_script": None})
    assert _sim(tmp_path) == 0
    assert blobs == {}
