from hammer.shell import pd_store_cli

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    FakeDB, db, tool_runs, _run_dag)
from tests.regression.test_a_syn_in_a_pulled_copy_leaves_the_callers_shared_bundle_alone import (
    _alice_and_bob_share_their_builds, _bundle_rtl, _write_rtl)


def test_a_pulled_copy_the_caller_shares_republishes_its_changes(tmp_path, db, tool_runs, monkeypatch) -> None:
    bob, summary = _alice_and_bob_share_their_builds(tmp_path, db, monkeypatch)
    pulled = tmp_path / "bob" / "build-from-alice"
    _write_rtl(pulled, "a | a")
    _run_dag(summary["dag_file"], tmp_path)
    assert "assign b = ~a;" in _bundle_rtl(db, "bob")

    assert pd_store_cli.main(["share", "build", "alice", "--obj-dir", str(pulled / "build")]) == 0
    assert "assign b = a | a;" in _bundle_rtl(db, "bob")
    _write_rtl(pulled, "a & a")
    _run_dag(summary["dag_file"], tmp_path)
    assert "assign b = a & a;" in _bundle_rtl(db, "bob")
