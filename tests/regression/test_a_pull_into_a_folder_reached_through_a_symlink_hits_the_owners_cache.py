from hammer.shell import pd_store_cli
from hammer.vlsi import design_share

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    db, tool_runs, _alice_design, _hammer, _run_dag)


def test_a_partner_whose_pull_folder_is_reached_through_a_symlink_restores_the_owners_syn_and_par(
        tmp_path, db, tool_runs, monkeypatch, capsys) -> None:
    alice = _alice_design(tmp_path)
    obj_a = alice / "build" / "dummy"
    monkeypatch.chdir(alice)
    _hammer("build", [str(alice / "env.yml")], [str(alice / "design.yml")], str(obj_a), tmp_path)
    _run_dag(str(obj_a / "hammer_dag.py"), tmp_path, through_par=True)
    alices_keys = list(db.blobs)
    bundle = design_share.bundle_from_dag(str(obj_a / "hammer_dag.py"), owner="alice")
    assert db.publish("dummy", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("dummy", "bob")

    db.user = "bob"
    (tmp_path / "nfs" / "bob").mkdir(parents=True)
    (tmp_path / "home").symlink_to(tmp_path / "nfs", target_is_directory=True)
    into = tmp_path / "home" / "bob" / "dummy-from-alice"
    assert pd_store_cli.main(["pull", "alice", "dummy", "--into", str(into)]) == 0
    capsys.readouterr()
    db.loads.clear()
    _run_dag(str(into / "build" / "dummy" / "hammer_dag.py"), tmp_path, through_par=True)
    assert db.loads == alices_keys
    assert len(tool_runs) == 2
