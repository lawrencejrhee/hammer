import io
import json
import tarfile
from pathlib import Path

from hammer.config import HammerJSONEncoder
from hammer.vlsi import design_share

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    FakeDB, db, tool_runs, _hammer, _run_dag)


def _course_project(root: Path, body: str) -> Path:
    """A course project: the vlsi dir is the working directory and every build dir is <project>/build."""
    work = root / "proj"
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


def _bundle_rtl(db, owner: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(db.bundles[(owner, "build")]["data"]), mode="r:gz") as tar:
        return tar.extractfile("work/rtl/top.v").read().decode()


def _alice_and_bob_share_their_builds(tmp_path: Path, db, monkeypatch):
    """Alice shares her build with Bob; Bob shares his own build with Alice and Carol, then pulls Alice's."""
    db.user = "alice"
    alice = _course_project(tmp_path / "alice", "a")
    monkeypatch.chdir(alice)
    _hammer("build", [str(alice / "env.yml")], [str(alice / "design.yml")], str(alice / "build"), tmp_path)
    _run_dag(str(alice / "build" / "hammer_dag.py"), tmp_path, through_par=True)
    bundle = design_share.bundle_from_dag(str(alice / "build" / "hammer_dag.py"), owner="alice")
    assert db.publish("build", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("build", "bob")

    db.user = "bob"
    bob = _course_project(tmp_path / "bob", "~a")
    monkeypatch.chdir(bob)
    _hammer("build", [str(bob / "env.yml")], [str(bob / "design.yml")], str(bob / "build"), tmp_path)
    bundle = design_share.bundle_from_dag(str(bob / "build" / "hammer_dag.py"), owner="bob")
    assert db.publish("build", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("build", "alice")
    db.share("build", "carol")
    _run_dag(str(bob / "build" / "hammer_dag.py"), tmp_path, through_par=True)
    monkeypatch.chdir(tmp_path / "bob")
    summary = design_share.pull("alice", "build", "build-from-alice", fetch=db.fetch)
    return bob, summary


def test_a_syn_in_a_pulled_copy_leaves_the_callers_shared_bundle_alone(tmp_path, db, tool_runs,
                                                                        monkeypatch) -> None:
    bob, summary = _alice_and_bob_share_their_builds(tmp_path, db, monkeypatch)
    bobs_bundle = db.bundles[("bob", "build")]["bundle_sha"]

    n_runs = len(tool_runs)
    _run_dag(summary["dag_file"], tmp_path, through_par=True)
    assert tool_runs[n_runs:] == []           # Alice's syn and par came from the cache
    assert db.bundles[("bob", "build")]["bundle_sha"] == bobs_bundle
    assert "assign b = ~a;" in _bundle_rtl(db, "bob")

    db.user = "carol"
    design_share.pull("bob", "build", str(tmp_path / "carol" / "bob-build"), fetch=db.fetch, build=False)
    assert "assign b = ~a;" in (tmp_path / "carol" / "bob-build" / "rtl" / "top.v").read_text()

    db.user = "bob"
    _write_rtl(bob, "~a & a")
    _run_dag(str(bob / "build" / "hammer_dag.py"), tmp_path)
    assert "assign b = ~a & a;" in _bundle_rtl(db, "bob")
    assert db.bundles[("bob", "build")]["manifest"]["source"]["work_dir"] == str(bob)
