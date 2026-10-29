import io
import os
import tarfile
from pathlib import Path

import pytest

from hammer.vlsi import CLIDriver, HammerDriver, HammerDriverOptions, design_share

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    FakeDB, db, tool_runs, _alice_design, _hammer, _run_dag)


def _convert(action: str, envs, source: str, dest: str, obj: str, out: Path) -> None:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=[action] + [a for e in envs for a in ("-e", e)]
                         + ["-p", source, "-o", dest, "--obj_dir", obj, "--log", str(out / "log.txt")])
    assert cm.value.code == 0, action


def _run_in_workspace(dag: dict, ws: str, out: Path) -> None:
    """The DAG's tasks with OBJ_DIR moved to a named workspace, from WORK_DIR, as `sledgehammer run
    --workspace` runs them."""
    envs = dag["ENV_CONFIGS"]
    old = os.getcwd()
    os.chdir(dag["WORK_DIR"])
    try:
        _hammer("syn", envs, dag["PROJ_CONFIGS"], ws, out)
        _convert("syn-to-par", envs, os.path.join(ws, "syn-rundir", "syn-output-full.json"),
                 os.path.join(ws, "par-input.json"), ws, out)
        _hammer("par", envs, [os.path.join(ws, "par-input.json")], ws, out)
        for stage in ("drc", "lvs"):
            _convert(f"par-to-{stage}", envs, os.path.join(ws, "par-rundir", "par-output-full.json"),
                     os.path.join(ws, f"{stage}-input.json"), ws, out)
            _hammer(stage, envs, [os.path.join(ws, f"{stage}-input.json")], ws, out)
    finally:
        os.chdir(old)


def test_a_workspace_run_never_shares_a_stage_input_as_the_design(tmp_path, db, tool_runs, monkeypatch) -> None:
    alice = _alice_design(tmp_path)
    obj_a = alice / "build" / "dummy"
    dag_file = str(obj_a / "hammer_dag.py")
    monkeypatch.chdir(alice)
    _hammer("build", [str(alice / "env.yml")], [str(alice / "design.yml")], str(obj_a), tmp_path)
    bundle = design_share.bundle_from_dag(dag_file, owner="alice")
    assert db.publish("dummy", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("dummy", "bob")

    monkeypatch.setenv("HAMMER_AIRFLOW_DESIGN", "dummy")
    ws = tmp_path / "alice" / "ws-exp1" / "dummy"
    ws.mkdir(parents=True)
    (alice / "rtl" / "top.v").write_text('`include "defs.vh"\n'
                                        'module dummy(input a, output b); assign b = ~a; endmodule\n')
    dag = design_share.read_dag(dag_file)
    _run_in_workspace(dag, str(ws), tmp_path)
    shared = db.bundles[("alice", "dummy")]
    assert shared["manifest"]["proj_confs_by_tools"] == {"default": ["work/design.yml"]}
    with tarfile.open(fileobj=io.BytesIO(shared["data"]), mode="r:gz") as tar:
        assert "assign b = ~a;" in tar.extractfile("work/rtl/top.v").read().decode()
    for stage in ("par", "drc", "lvs"):
        driver = HammerDriver(HammerDriverOptions(
            environment_configs=list(dag["ENV_CONFIGS"]), project_configs=[str(ws / f"{stage}-input.json")],
            log_file=str(tmp_path / "log.txt"), obj_dir=str(ws)))
        assert design_share.republish_if_shared(driver) is None
    assert db.bundles[("alice", "dummy")]["bundle_sha"] == shared["bundle_sha"]
    monkeypatch.delenv("HAMMER_AIRFLOW_DESIGN")

    db.user = "bob"
    into = tmp_path / "bob" / "pulled"
    summary = design_share.pull("alice", "dummy", str(into), fetch=db.fetch)
    assert design_share.read_dag(summary["dag_file"])["PROJ_CONFIGS"][0] == str(into / "design.yml")
    n_runs = len(tool_runs)
    _run_dag(summary["dag_file"], tmp_path, through_par=True)
    assert tool_runs[n_runs:] == []
