import json

from hammer.vlsi import design_share

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    FakeDB, db, tool_runs, _alice_design, _hammer, _run_dag)


def test_a_par_only_change_reaches_the_partners_pull(tmp_path, db, tool_runs, monkeypatch) -> None:
    alice = _alice_design(tmp_path)
    obj_a = alice / "build" / "dummy"
    monkeypatch.chdir(alice)
    _hammer("build", [str(alice / "env.yml")], [str(alice / "design.yml")], str(obj_a), tmp_path)
    _run_dag(str(obj_a / "hammer_dag.py"), tmp_path, through_par=True)
    bundle = design_share.bundle_from_dag(str(obj_a / "hammer_dag.py"), owner="alice")
    assert db.publish("dummy", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("dummy", "bob")
    first = db.bundles[("alice", "dummy")]["bundle_sha"]
    old_blobs = set(db.blobs)

    config = json.loads((alice / "design.yml").read_text())
    config["par.blockage_spacing"] = 2.5
    (alice / "design.yml").write_text(json.dumps(config, indent=4))
    n_runs = len(tool_runs)
    _run_dag(str(obj_a / "hammer_dag.py"), tmp_path, through_par=True)
    assert [stage for stage, _ in tool_runs[n_runs:]] == ["run_par"]
    new_par_blob = (set(db.blobs) - old_blobs).pop()
    assert db.bundles[("alice", "dummy")]["bundle_sha"] != first

    db.user = "bob"
    into = tmp_path / "bob" / "pulled"
    summary = design_share.pull("alice", "dummy", str(into), fetch=db.fetch)
    assert json.loads((into / "design.yml").read_text())["par.blockage_spacing"] == 2.5
    db.loads.clear()
    n_runs = len(tool_runs)
    _run_dag(summary["dag_file"], tmp_path, through_par=True)
    assert db.loads[-1] == new_par_blob and tool_runs[n_runs:] == []
