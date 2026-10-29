from pathlib import Path

from hammer.vlsi import design_share

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    FakeDB, db, tool_runs, _hammer, _run_dag)
from tests.regression.test_a_syn_in_another_checkout_leaves_the_shared_bundle_alone import (
    _bundle_rtl, _checkout, _write_rtl)


def _spell(dag_file: Path, old: Path, new: Path) -> None:
    """Name the checkout in the DAG through another path to it, as a DAG written on another host does."""
    dag_file.write_text(dag_file.read_text().replace(str(old), str(new)))


def test_a_checkout_reached_through_a_symlink_keeps_republishing(tmp_path, db, tool_runs, monkeypatch) -> None:
    db.user = "bob"
    disk, home = tmp_path / "disk", tmp_path / "home"
    project = _checkout(disk / "bob" / "project", "a")
    lab = _checkout(disk / "bob" / "lab4", "~a")
    home.symlink_to(disk)
    for work in (project, lab):
        monkeypatch.chdir(work)
        _hammer("build", [str(work / "env.yml")], [str(work / "design.yml")], str(work / "build"), tmp_path)
    dag = project / "build" / "hammer_dag.py"
    bundle = design_share.bundle_from_dag(str(dag), owner="bob")
    assert db.publish("build", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("build", "alice")

    _spell(dag, disk, home)
    _write_rtl(project, "a & a")
    _run_dag(str(dag), tmp_path)
    assert "assign b = a & a;" in _bundle_rtl(db)
    assert db.bundles[("bob", "build")]["manifest"]["source"]["work_dir"] == str(home / "bob" / "project")

    _spell(dag, home, disk)
    _write_rtl(project, "a | a")
    _run_dag(str(dag), tmp_path)
    assert "assign b = a | a;" in _bundle_rtl(db)

    _run_dag(str(lab / "build" / "hammer_dag.py"), tmp_path)
    assert "assign b = a | a;" in _bundle_rtl(db)
