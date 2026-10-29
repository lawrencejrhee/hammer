import io
import json
import tarfile
from pathlib import Path

from hammer.config import HammerJSONEncoder
from hammer.shell import pd_store_cli
from hammer.vlsi import design_share

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    FakeDB, db, tool_runs, _hammer, _run_dag)


def _checkout(work: Path, body: str) -> Path:
    """One of Bob's checkouts; each builds into <checkout>/build, so every one is the design 'build'."""
    (work / "rtl").mkdir(parents=True)
    _write_rtl(work, body)
    (work / "env.yml").write_text("{}\n")
    (work / "design.yml").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.build_system": "sledgehammer",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(work / "rtl" / "top.v")],
    }, cls=HammerJSONEncoder, indent=4))
    return work


def _write_rtl(work: Path, body: str) -> None:
    (work / "rtl" / "top.v").write_text(f"module dummy(input a, output b); assign b = {body}; endmodule\n")


def _bundle_rtl(db) -> str:
    with tarfile.open(fileobj=io.BytesIO(db.bundles[("bob", "build")]["data"]), mode="r:gz") as tar:
        return tar.extractfile("work/rtl/top.v").read().decode()


def test_a_syn_in_another_checkout_leaves_the_shared_bundle_alone(tmp_path, db, tool_runs, monkeypatch,
                                                                  capsys) -> None:
    db.user = "bob"
    project = _checkout(tmp_path / "bob" / "project", "a")
    lab = _checkout(tmp_path / "bob" / "lab4", "~a")
    for work in (project, lab):
        monkeypatch.chdir(work)
        _hammer("build", [str(work / "env.yml")], [str(work / "design.yml")], str(work / "build"), tmp_path)
    bundle = design_share.bundle_from_dag(str(project / "build" / "hammer_dag.py"), owner="bob")
    assert db.publish("build", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("build", "alice")
    shared = db.bundles[("bob", "build")]["bundle_sha"]
    capsys.readouterr()

    _run_dag(str(lab / "build" / "hammer_dag.py"), tmp_path)
    assert db.bundles[("bob", "build")]["bundle_sha"] == shared
    assert f"it was published from {project}, not from {lab}" in capsys.readouterr().out
    assert "assign b = a;" in _bundle_rtl(db)

    _write_rtl(project, "a & a")
    _run_dag(str(project / "build" / "hammer_dag.py"), tmp_path)
    assert "assign b = a & a;" in _bundle_rtl(db)


def test_studio_publish_moves_the_shared_bundle_to_another_checkout(tmp_path, db, tool_runs, monkeypatch) -> None:
    db.user = "bob"
    project = _checkout(tmp_path / "bob" / "project", "a")
    lab = _checkout(tmp_path / "bob" / "lab4", "~a")
    for work in (project, lab):
        monkeypatch.chdir(work)
        _hammer("build", [str(work / "env.yml")], [str(work / "design.yml")], str(work / "build"), tmp_path)
    assert pd_store_cli.main(["share", "build", "alice", "--obj-dir", str(project / "build")]) == 0
    assert pd_store_cli.main(["publish", "build", "--obj-dir", str(lab / "build")]) == 0
    assert "assign b = ~a;" in _bundle_rtl(db)

    _write_rtl(project, "a & a")
    _run_dag(str(project / "build" / "hammer_dag.py"), tmp_path)
    assert "assign b = ~a;" in _bundle_rtl(db)
    _write_rtl(lab, "~a & a")
    _run_dag(str(lab / "build" / "hammer_dag.py"), tmp_path)
    assert "assign b = ~a & a;" in _bundle_rtl(db)
