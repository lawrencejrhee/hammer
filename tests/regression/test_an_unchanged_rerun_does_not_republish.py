import json
from pathlib import Path

import pytest

from hammer.synthesis import mocksynth
from hammer.vlsi import CLIDriver, design_share, pd_store

from tests.regression.test_pulled_design_hits_the_owners_cache import (  # noqa: F401
    FakeDB, db, tool_runs, _alice_design, _hammer, _run_dag)
from tests.regression.test_a_drc_or_lvs_only_change_reaches_the_partners_pull import _run_check

SITE_TOOLS = (("synthesis", "sitesyn", "hammer.synthesis.mocksynth", "genus"),
              ("par", "sitepar", "hammer.par.nop", "innovus"),
              ("drc", "sitedrc", "hammer.drc.nop", "pegasus"),
              ("lvs", "sitelvs", "hammer.lvs.nop", "pegasus"))


def _site_tools(tmp_path: Path, monkeypatch) -> dict:
    """Tool plugins whose defaults name their binary by absolute path, as genus_bin, innovus_bin and pegasus_bin do."""
    plugins = tmp_path / "plugins"
    settings = {}
    for stage, name, base, binary in SITE_TOOLS:
        exe = tmp_path / "tools" / binary / "bin" / binary
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!/bin/sh\n")
        (plugins / name).mkdir(parents=True)
        (plugins / name / "__init__.py").write_text(
            f"from {base} import tool as Base\n\n\nclass Site(Base):\n    pass\n\n\ntool = Site\n")
        defaults = (Path(mocksynth.__file__).parent / "defaults.yml").read_text() if stage == "synthesis" else ""
        (plugins / name / "defaults.yml").write_text(f"{defaults}\n{stage}.{name}:\n  {binary}_bin: \"{exe}\"\n")
        settings[f"vlsi.core.{stage}_tool"] = name
    monkeypatch.syspath_prepend(str(plugins))
    return settings


def _run_all(dag_file: str, out: Path) -> None:
    _run_dag(dag_file, out, through_par=True)
    _run_check(dag_file, out, "drc")
    _run_check(dag_file, out, "lvs")


def _shared_site_build(tmp_path: Path, db, monkeypatch):
    """Alice's design on the site tools, built through lvs and shared, recording every later pack and upload."""
    alice = _alice_design(tmp_path)
    config = json.loads((alice / "design.yml").read_text())
    config.update(_site_tools(tmp_path, monkeypatch))
    (alice / "design.yml").write_text(json.dumps(config, indent=4))
    obj_a = alice / "build" / "dummy"
    monkeypatch.chdir(alice)
    _hammer("build", [str(alice / "env.yml")], [str(alice / "design.yml")], str(obj_a), tmp_path)
    _run_all(str(obj_a / "hammer_dag.py"), tmp_path)
    bundle = design_share.bundle_from_dag(str(obj_a / "hammer_dag.py"), owner="alice")
    assert db.publish("dummy", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("dummy", "bob")
    uploads, packs = [], []

    def publish(design, manifest, data, bundle_sha):
        uploaded = db.publish(design, manifest, data, bundle_sha)
        if uploaded:
            uploads.append(bundle_sha)
        return uploaded
    monkeypatch.setattr(pd_store, "publish_design_bundle", publish)
    pack = design_share._pack
    monkeypatch.setattr(design_share, "_pack", lambda files: packs.append(sorted(files)) or pack(files))
    return alice, obj_a, uploads, packs


def test_an_unchanged_rerun_does_not_republish(tmp_path, db, tool_runs, monkeypatch) -> None:
    alice, obj_a, uploads, packs = _shared_site_build(tmp_path, db, monkeypatch)
    sha = db.bundles[("alice", "dummy")]["bundle_sha"]
    n_runs = len(tool_runs)
    _run_all(str(obj_a / "hammer_dag.py"), tmp_path)
    assert tool_runs[n_runs:] == []

    for stage in ("syn", "par", "drc", "lvs", "syn", "par"):
        config = str(alice / "design.yml") if stage == "syn" else str(obj_a / f"{stage}-input.json")
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=[stage, "-e", str(alice / "env.yml"), "-p", config, "--force", "--obj_dir",
                                   str(obj_a), "-o", str(tmp_path / f"{stage}-out.json"), "--log",
                                   str(tmp_path / "log.txt")])
        assert cm.value.code == 0, stage
    assert [stage for stage, _ in tool_runs[n_runs:]] == ["run_synthesis", "run_par"] * 2
    assert uploads == [] and packs == []
    assert db.bundles[("alice", "dummy")]["bundle_sha"] == sha
