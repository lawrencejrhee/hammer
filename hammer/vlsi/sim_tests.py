"""Run a design's test programs (sim.tests) on the simulator the sim action compiled.

Tests always run. Their results are never cached: simulations commonly draw a new random seed each run, so a stored
pass would not be a new sample. Only the compiled simulator is cached, by the sim action.
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import mmap
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Sequence

# What running tests or a waveform-dumping testbench leaves in a run directory; none of it is compile output.
_RUN_FILE_SUFFIXES = (".vpd", ".fsdb", ".vcd", ".saif")
_DEFAULT_OUTPUT_DIR = "tests"


class SimTestError(ValueError):
    """The test spec or the selection is wrong, so nothing can run."""


@dataclasses.dataclass(frozen=True)
class SimTest:
    suite: str
    name: str
    program: Optional[str]
    flags: List[str]
    pass_regex: str


def _suite_tests(suite: Dict[str, Any]) -> List[SimTest]:
    name = suite.get("name")
    if not isinstance(name, str) or not name:
        raise SimTestError(f"sim.tests.suites: a suite has no name: {suite!r}")
    pass_regex = suite.get("pass_regex")
    if not isinstance(pass_regex, str) or not pass_regex:
        raise SimTestError(f"sim.tests.suites: suite {name} has no pass_regex")
    try:
        re.compile(pass_regex)
    except re.error as e:
        raise SimTestError(f"sim.tests.suites: suite {name} has a bad pass_regex: {e}") from e
    tests = []
    for test in suite.get("tests") or [name]:
        program = None
        if suite.get("program"):
            program = os.path.abspath(str(suite["program"]).replace("{test}", str(test)).replace("{suite}", name))
        flags = [str(flag).replace("{test}", str(test)).replace("{suite}", name).replace("{program}", program or "")
                 for flag in suite.get("flags") or []]
        tests.append(SimTest(name, str(test), program, flags, pass_regex))
    return tests


def select_tests(suites: Sequence[Dict[str, Any]], selections: Sequence[str]) -> List[SimTest]:
    """The tests that selections name, each once and in selection order. A selection is `suite`, `suite:all` or
    `suite:test,test`."""
    by_suite = {tests[0].suite: tests for tests in (_suite_tests(suite) for suite in suites)}
    chosen: Dict[Any, SimTest] = {}
    for selection in selections:
        suite, _, names = selection.partition(":")
        if suite not in by_suite:
            raise SimTestError(f"no sim test suite named {suite!r}. Suites: {', '.join(sorted(by_suite))}")
        tests = {test.name: test for test in by_suite[suite]}
        wanted = list(tests) if names in ("", "all") else [n for n in names.split(",") if n]
        unknown = [n for n in wanted if n not in tests]
        if unknown:
            raise SimTestError(f"suite {suite} has no test {', '.join(unknown)}. Tests: {', '.join(tests)}")
        for test_name in wanted:
            chosen[(suite, test_name)] = tests[test_name]
    return list(chosen.values())


def clear_run_files(rundir: str) -> None:
    """Remove what earlier test runs left in rundir, so a compile stored from it holds compile output only."""
    shutil.rmtree(os.path.join(rundir, _DEFAULT_OUTPUT_DIR), ignore_errors=True)
    try:
        names = os.listdir(rundir)
    except OSError:
        return
    for name in names:
        if name.endswith(_RUN_FILE_SUFFIXES):
            try:
                os.remove(os.path.join(rundir, name))
            except OSError:
                pass


def _matches(path: str, regex: "re.Pattern[bytes]") -> bool:
    """Whether regex matches in the file at path, which may be too large to read into memory."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        return regex.search(data) is not None  # type: ignore[call-overload]


def _run_test(test: SimTest, command: List[str], rundir: str, output_root: str, env: Dict[str, str]) -> Dict[str, Any]:
    directory = os.path.join(output_root, test.suite)
    os.makedirs(directory, exist_ok=True)
    out_path = os.path.join(directory, f"{test.name}.out")
    log_path = os.path.join(directory, f"{test.name}.log")
    result: Dict[str, Any] = {"suite": test.suite, "test": test.name, "out": out_path, "log": log_path,
                              "command": command + test.flags, "exit_code": None, "seconds": 0.0}
    if test.program is not None and not os.path.isfile(test.program):
        result.update(status="ERROR", reason=f"no program at {test.program}")
        return result
    start = time.monotonic()
    try:
        with open(out_path, "wb") as out, open(log_path, "wb") as log:
            proc = subprocess.run(command + test.flags, cwd=rundir, env=env, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=out, check=False)
    except OSError as e:
        result.update(status="ERROR", reason=f"could not run the simulator: {e}", seconds=time.monotonic() - start)
        return result
    result.update(exit_code=proc.returncode, seconds=round(time.monotonic() - start, 3))
    regex = re.compile(test.pass_regex.encode())
    passed = proc.returncode == 0 and (_matches(out_path, regex) or _matches(log_path, regex))
    result["status"] = "PASSED" if passed else "FAILED"
    return result


def _prepare(suites: Sequence[Dict[str, Any]], names: Sequence[str], log: Any) -> Dict[str, str]:
    """Run each selected suite's prepare commands once; returns why each suite whose commands failed cannot run."""
    failed: Dict[str, str] = {}
    for suite in suites:
        if suite.get("name") not in names:
            continue
        for command in suite.get("prepare") or []:
            log.info(f"Preparing sim test suite {suite['name']}: {command}")
            done = subprocess.run(str(command), shell=True, stdin=subprocess.DEVNULL, check=False)
            if done.returncode != 0:
                failed[suite["name"]] = f"prepare command failed with exit code {done.returncode}: {command}"
                log.error(f"Sim test suite {suite['name']}: {failed[suite['name']]}")
                break
    return failed


def _output_root(output_dir: Optional[str], rundir: str) -> str:
    if output_dir:
        return os.path.join(os.path.abspath(output_dir), os.path.basename(rundir.rstrip("/")))
    return os.path.join(rundir, _DEFAULT_OUTPUT_DIR)


def run_selected(driver: Any, selections: Sequence[str]) -> bool:
    """Run the selected tests, every one even after a failure, and write <output root>/results.json. True if all
    passed."""
    tool = driver.sim_tool
    log = driver.log
    suites = driver.database.get_setting("sim.tests.suites", nullvalue=[], check_type=False) or []
    try:
        tests = select_tests(suites, selections)
    except SimTestError as e:
        log.error(str(e))
        return False
    simulator = getattr(tool, "simulator_executable_path", None)
    if simulator is None:
        log.error(f"The sim tool {tool.name} cannot run sim.tests.")
        return False
    if not os.path.isfile(simulator):
        log.error(f"No compiled simulator at {simulator}, so no sim test can run.")
        return False
    get = driver.database.get_setting
    command = ([str(a) for a in get("sim.tests.run_prefix", nullvalue=[], check_type=False) or []] + [simulator]
               + [str(a) for a in get("sim.tests.run_flags", nullvalue=[], check_type=False) or []])
    output_root = _output_root(get("sim.tests.output_dir", nullvalue=None, check_type=False), tool.run_dir)
    os.makedirs(output_root, exist_ok=True)

    cannot_run = _prepare(suites, sorted({test.suite for test in tests}), log)
    env = tool._subprocess_env

    def one(test: SimTest) -> Dict[str, Any]:
        if test.suite in cannot_run:
            return {"suite": test.suite, "test": test.name, "status": "ERROR", "reason": cannot_run[test.suite],
                    "exit_code": None, "seconds": 0.0, "out": None, "log": None}
        result = _run_test(test, command, tool.run_dir, output_root, env)
        log.info(f"[{result['status']}] {test.suite}/{test.name} ({result['seconds']:.1f} s)"
                 + (f": {result['reason']}" if result.get("reason") else ""))
        return result

    parallel = int(get("sim.inputs.parallel_runs", nullvalue=1, check_type=False) or 1)
    workers = len(tests) if parallel < 0 else max(parallel, 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        results = list(pool.map(one, tests))

    with open(os.path.join(output_root, "results.json"), "w") as f:
        json.dump({"results": results}, f, indent=2)
    bad = [r for r in results if r["status"] != "PASSED"]
    log.info(f"Sim tests: {len(results) - len(bad)} passed, {len(bad)} did not, of {len(results)}. "
             f"Results: {os.path.join(output_root, 'results.json')}")
    for result in bad:
        log.error(f"Sim test {result['suite']}/{result['test']}: {result['status']}"
                  + (f" ({result['reason']})" if result.get("reason") else ""))
    return not bad
