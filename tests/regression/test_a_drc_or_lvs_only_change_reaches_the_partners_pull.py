import json
import os
from pathlib import Path

import pytest

from hammer.vlsi import CLIDriver, HammerDriver, design_share

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    FakeDB, db, tool_runs, _alice_design, _hammer, _run_dag)


def _run_check(dag_file: str, out: Path, stage: str) -> None:
    """par-to-<stage> and <stage> as the DAG's tasks run them, after its syn and par."""
    dag = design_share.read_dag(dag_file)
    obj = dag["OBJ_DIR"]
    envs = [a for e in dag["ENV_CONFIGS"] for a in ("-e", e)]
    stage_in = os.path.join(obj, f"{stage}-input.json")
    old = os.getcwd()
    os.chdir(dag["WORK_DIR"])
    try:
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=[f"par-to-{stage}"] + envs
                             + ["-p", os.path.join(obj, "par-rundir", "par-output-full.json"), "-o", stage_in,
                                "--obj_dir", obj, "--log", str(out / "log.txt")])
        assert cm.value.code == 0
        _hammer(stage, dag["ENV_CONFIGS"], [stage_in], obj, out)
    finally:
        os.chdir(old)


@pytest.mark.parametrize("stage,key", [("drc", "drc.inputs.additional_drc_text"),
                                       ("lvs", "lvs.inputs.additional_lvs_text")])
def test_a_drc_or_lvs_only_change_reaches_the_partners_pull(stage, key, tmp_path, db, tool_runs,
                                                            monkeypatch) -> None:
    runs = []
    real = getattr(HammerDriver, f"run_{stage}")
    monkeypatch.setattr(HammerDriver, f"run_{stage}",
                        lambda self, *a, **k: runs.append(stage) or real(self, *a, **k))
    alice = _alice_design(tmp_path)
    obj_a = alice / "build" / "dummy"
    dag_file = str(obj_a / "hammer_dag.py")
    monkeypatch.chdir(alice)
    _hammer("build", [str(alice / "env.yml")], [str(alice / "design.yml")], str(obj_a), tmp_path)
    _run_dag(dag_file, tmp_path, through_par=True)
    _run_check(dag_file, tmp_path, stage)
    bundle = design_share.bundle_from_dag(dag_file, owner="alice")
    assert db.publish("dummy", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("dummy", "bob")
    first = db.bundles[("alice", "dummy")]["bundle_sha"]

    config = json.loads((alice / "design.yml").read_text())
    config[key] = "# checked by the owner"
    (alice / "design.yml").write_text(json.dumps(config, indent=4))
    n_runs, n_checks = len(tool_runs), len(runs)
    _run_dag(dag_file, tmp_path, through_par=True)
    _run_check(dag_file, tmp_path, stage)
    assert tool_runs[n_runs:] == [] and runs[n_checks:] == [stage]
    assert db.bundles[("alice", "dummy")]["bundle_sha"] != first

    db.user = "bob"
    into = tmp_path / "bob" / "pulled"
    design_share.pull("alice", "dummy", str(into), fetch=db.fetch, build=False)
    assert json.loads((into / "design.yml").read_text())[key] == "# checked by the owner"
