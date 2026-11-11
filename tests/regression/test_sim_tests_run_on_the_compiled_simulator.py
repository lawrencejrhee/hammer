import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, sim_tests

SIMV = """#!/bin/sh
echo "start $*" >> {trace}
pwd >> {trace}
case " $* " in *+t=fail*) echo nope >&2; echo "end $*" >> {trace}; exit 0;; *+t=crash*) exit 3;; esac
echo "stdout line"
echo "*** PASSED ***" >&2
echo "end $*" >> {trace}
"""


def _write(tmp_path: Path, suites, **extra) -> None:
    (tmp_path / "tb.v").write_text("module tb; endmodule\n")
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
        "sim.mocksim.simv_script": SIMV.format(trace=tmp_path / "trace"),
        "sim.tests.suites": suites,
    }
    config.update(extra)
    (tmp_path / "config.json").write_text(json.dumps(config, cls=HammerJSONEncoder))


def _sim(tmp_path: Path, *flags: str) -> int:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["sim", *flags, "-o", str(tmp_path / "output.json"), "-p", str(tmp_path / "config.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt"),
                               "--sim_rundir", str(tmp_path / "obj" / "sim-rundir")])
    return cm.value.code


def _results(tmp_path: Path, root: str = "obj/sim-rundir/tests") -> dict:
    data = json.loads((tmp_path / root / "results.json").read_text())
    return {f"{r['suite']}/{r['test']}": r for r in data["results"]}


def _suite(**extra) -> dict:
    return {"name": "asm", "tests": ["a", "b"], "program": "{test}.hex", "flags": ["+loadmem={program}", "+t={test}"],
            "pass_regex": r"\*\*\* PASSED \*\*\*", **extra}


@pytest.fixture(autouse=True)
def _here(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("HAMMER_PD_CACHE", "HAMMER_AIRFLOW_DESIGN"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "a.hex").write_text("0\n")
    (tmp_path / "b.hex").write_text("0\n")


def _never(*args, **kwargs):
    raise AssertionError("the test runner must not be called")


def test_a_design_with_no_suites_never_calls_the_runner(tmp_path, monkeypatch) -> None:
    _write(tmp_path, [])
    monkeypatch.setattr(sim_tests, "run_selected", _never)
    monkeypatch.setattr(sim_tests, "clear_run_files", _never)
    assert _sim(tmp_path) == 0
    assert not (tmp_path / "trace").exists()
    assert not (tmp_path / "obj" / "sim-rundir" / "tests").exists()


def test_selecting_tests_on_a_design_with_no_suites_is_an_error(tmp_path, capsys) -> None:
    _write(tmp_path, [])
    assert _sim(tmp_path, "--sim_tests", "asm") != 0
    assert "no sim.tests.suites" in capsys.readouterr().out


def test_suites_with_nothing_selected_only_compile(tmp_path, capsys) -> None:
    _write(tmp_path, [_suite()])
    assert _sim(tmp_path) == 0
    assert not (tmp_path / "trace").exists()
    assert "No sim tests selected" in capsys.readouterr().out


def test_selected_tests_run_from_the_rundir_and_are_recorded(tmp_path) -> None:
    _write(tmp_path, [_suite()], **{"sim.tests.run_flags": ["-q"]})
    assert _sim(tmp_path, "--sim_tests", "asm:all") == 0
    results = _results(tmp_path)
    assert {name: r["status"] for name, r in results.items()} == {"asm/a": "PASSED", "asm/b": "PASSED"}
    trace = (tmp_path / "trace").read_text().splitlines()
    assert trace[0] == f"start -q +loadmem={tmp_path / 'a.hex'} +t=a"
    assert trace[1] == str((tmp_path / "obj" / "sim-rundir").resolve())
    assert "*** PASSED ***" in Path(results["asm/a"]["out"]).read_text()
    assert "stdout line" in Path(results["asm/a"]["log"]).read_text()
    assert (tmp_path / "obj" / "sim-rundir" / "sim-output-full.json").is_file()


def test_tests_run_one_at_a_time_by_default(tmp_path) -> None:
    _write(tmp_path, [_suite()])
    assert _sim(tmp_path, "--sim_tests", "asm") == 0
    events = [line.split()[0] for line in (tmp_path / "trace").read_text().splitlines() if line[:3] in ("sta", "end")]
    assert events == ["start", "end", "start", "end"]


def test_a_failing_test_fails_the_action_and_the_rest_still_run(tmp_path) -> None:
    _write(tmp_path, [_suite(tests=["fail", "b"])])
    (tmp_path / "fail.hex").write_text("0\n")
    assert _sim(tmp_path, "--sim_tests", "asm") != 0
    results = _results(tmp_path)
    assert results["asm/fail"]["status"] == "FAILED"
    assert results["asm/b"]["status"] == "PASSED"


def test_a_suite_with_no_pass_regex_is_an_error(tmp_path, capsys) -> None:
    _write(tmp_path, [_suite(pass_regex="")])
    assert _sim(tmp_path, "--sim_tests", "asm") != 0
    assert "has no pass_regex" in capsys.readouterr().out


def test_a_nonzero_exit_is_a_failure(tmp_path) -> None:
    _write(tmp_path, [_suite(tests=["crash"])])
    (tmp_path / "crash.hex").write_text("0\n")
    assert _sim(tmp_path, "--sim_tests", "asm") != 0
    assert _results(tmp_path)["asm/crash"]["exit_code"] == 3


def test_a_missing_program_is_an_error_for_that_test_only(tmp_path) -> None:
    _write(tmp_path, [_suite(tests=["a", "gone"])])
    assert _sim(tmp_path, "--sim_tests", "asm") != 0
    results = _results(tmp_path)
    assert results["asm/a"]["status"] == "PASSED"
    assert results["asm/gone"]["status"] == "ERROR"


def test_prepare_runs_once_from_the_working_directory_before_its_tests(tmp_path) -> None:
    _write(tmp_path, [_suite(tests=["built"], prepare=["echo x >> prepared; cp a.hex built.hex"])])
    assert _sim(tmp_path, "--sim_tests", "asm") == 0
    assert (tmp_path / "prepared").read_text() == "x\n"


def test_a_failed_prepare_fails_its_suite_without_running_it(tmp_path) -> None:
    _write(tmp_path, [_suite(prepare=["exit 7"])])
    assert _sim(tmp_path, "--sim_tests", "asm") != 0
    assert _results(tmp_path)["asm/a"]["status"] == "ERROR"
    assert not (tmp_path / "trace").exists()


def test_a_suite_with_no_programs_is_one_test_named_after_it(tmp_path) -> None:
    _write(tmp_path, [{"name": "alu", "flags": ["+max-cycles=1000"], "pass_regex": "PASSED"}])
    assert _sim(tmp_path, "--sim_tests", "alu") == 0
    assert _results(tmp_path)["alu/alu"]["status"] == "PASSED"


def test_only_the_named_tests_run(tmp_path) -> None:
    _write(tmp_path, [_suite()])
    assert _sim(tmp_path, "--sim_tests", "asm:b") == 0
    assert list(_results(tmp_path)) == ["asm/b"]


@pytest.mark.parametrize("selection, message", [("nope", "no sim test suite"), ("asm:nope", "has no test")])
def test_a_selection_that_names_nothing_is_an_error(tmp_path, capsys, selection, message) -> None:
    _write(tmp_path, [_suite()])
    assert _sim(tmp_path, "--sim_tests", selection) != 0
    assert message in capsys.readouterr().out


def test_output_dir_keeps_each_stage_apart(tmp_path) -> None:
    _write(tmp_path, [_suite()], **{"sim.tests.output_dir": str(tmp_path / "fast")})
    assert _sim(tmp_path, "--sim_tests", "asm") == 0
    assert (tmp_path / "fast" / "sim-rundir" / "asm" / "a.out").is_file()
