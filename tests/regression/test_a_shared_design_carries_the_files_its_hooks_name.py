import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Optional

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import HammerDriver, design_share, pd_cache, pd_store

DRIVER = '''import os
from pathlib import Path
from typing import List

from hammer.vlsi import CLIDriver, HammerTool, HammerToolHookAction

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_TCL = {site!r}
{defs}

def syn_extra(x: HammerTool) -> bool:
    with open({path}) as f:
        text = f.read()
    with open(os.path.join(x.run_dir, "syn_extra.used"), "w") as f:
        f.write(text)
    return True


class ProjectDriver(CLIDriver):
    def get_extra_synthesis_hooks(self) -> List[HammerToolHookAction]:
        return [HammerTool.make_pre_insertion_hook("step1", syn_extra)]


if __name__ == "__main__":
    ProjectDriver().main()
'''
# How the hook names scripts/syn_extra.tcl: (module-level definitions, open()'s argument).
IDIOMS = {
    "join": ("", 'os.path.join(HERE, "scripts/syn_extra.tcl")'),
    "fstring": ("", 'f"{HERE}/scripts/syn_extra.tcl"'),
    "variable": ('SYN_TCL = HERE + "/scripts/syn_extra.tcl"\n', "SYN_TCL"),
    "concat": ("", 'HERE + "/scripts/syn_extra.tcl"'),
    "command": ("", 'f"source {HERE}/scripts/syn_extra.tcl".split()[1]'),
    "dir_fstring": ('TCL_DIR = os.path.join(HERE, "scripts")\n', 'f"{TCL_DIR}/syn_extra.tcl"'),
    "dir_join": ('TCL_DIR = os.path.join(HERE, "scripts")\nSYN_TCL = os.path.join(TCL_DIR, "syn_extra.tcl")\n',
                 "SYN_TCL"),
    "dir_path": ('TCL_DIR = Path(__file__).parent / "scripts"\nSYN_TCL = TCL_DIR / "syn_extra.tcl"\n', "SYN_TCL"),
}
TCL = "set_db syn_generic_effort high\n"


class FakeDB:
    """The shared database in memory, showing each user what the row-level security lets them see."""

    def __init__(self) -> None:
        self.user = "alice"
        self.blobs: dict = {}
        self.bundles: dict = {}
        self.shares: set = set()
        self.loads: list = []

    def store_stage_blob(self, stage, key, data, duration_seconds=None, cpu_seconds=None, **_):
        self.blobs[key] = (stage, data, duration_seconds, cpu_seconds)

    def load_stage_blob(self, key):
        self.loads.append(key)
        return self.blobs.get(key)

    def publish(self, design, manifest, data, bundle_sha):
        self.bundles[(self.user, design)] = {"manifest": json.loads(json.dumps(manifest)), "data": data,
                                             "bundle_sha": bundle_sha, "updated_at": "2026-10-14T00:00:00"}
        return True

    def fetch(self, owner, design):
        if owner != self.user and (owner, design, self.user) not in self.shares:
            return None
        return self.bundles.get((owner, design))

    def share(self, design, grantee):
        self.shares.add((self.user, design, grantee))


@pytest.fixture
def db(monkeypatch, tmp_path):
    fake = FakeDB()
    monkeypatch.setattr(pd_store, "store_stage_blob", fake.store_stage_blob)
    monkeypatch.setattr(pd_store, "load_stage_blob", fake.load_stage_blob)
    monkeypatch.setattr(pd_store, "publish_design_bundle", fake.publish)
    monkeypatch.setattr(pd_store, "fetch_design_bundle", fake.fetch)
    monkeypatch.setattr(pd_store, "design_has_shares",
                        lambda design: any(o == fake.user and d == design for o, d, _ in fake.shares))
    monkeypatch.setattr(pd_store, "current_db_user", lambda: fake.user)
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    monkeypatch.setenv("HAMMER_CODE_FP_CACHE", "off")
    monkeypatch.setenv("HAMMER_DAGS_FOLDER", str(tmp_path / "dags"))
    for name in ("HAMMER_AIRFLOW_DESIGN", "HAMMER_AIRFLOW_RUN_ID", "AIRFLOW_CTX_DAG_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    return fake


@pytest.fixture
def tool_runs(monkeypatch):
    runs = []
    real = HammerDriver.run_synthesis

    def spy(self, *args, **kwargs):
        runs.append(os.getcwd())
        return real(self, *args, **kwargs)
    monkeypatch.setattr(HammerDriver, "run_synthesis", spy)
    return runs


def _alice_design(tmp_path: Path, idiom: str = "join", driver: Optional[str] = None) -> Path:
    """Alice's project: her own driver, whose syn hook reads scripts/syn_extra.tcl beside it."""
    work = tmp_path / "alice" / "proj"
    (work / "src").mkdir(parents=True)
    (work / "scripts").mkdir()
    (work / "scripts" / "syn_extra.tcl").write_text(TCL)
    site = tmp_path / "site" / "common.tcl"
    site.parent.mkdir()
    site.write_text("set common 1\n")
    defs, path = IDIOMS[idiom]
    (work / "project_driver.py").write_text((driver or DRIVER).format(site=str(site), defs=defs, path=path))
    (work / "src" / "top.v").write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (work / "env.yml").write_text("{}\n")
    (work / "design.yml").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.build_system": "sledgehammer",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(work / "src" / "top.v")],
    }, cls=HammerJSONEncoder, indent=4))
    return work


def _driver(path: str, monkeypatch):
    """The project's driver module, loaded under one name for every copy as the DAG's `python <driver>` does."""
    spec = importlib.util.spec_from_file_location("project_driver", path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "project_driver", mod)
    spec.loader.exec_module(mod)
    return mod


def _main(driver_file: str, args, out: Path, monkeypatch) -> int:
    with pytest.raises(SystemExit) as cm:
        _driver(driver_file, monkeypatch).ProjectDriver().main(
            args=args + ["-o", str(out / f"{args[0]}-out.json"), "--log", str(out / "log.txt")])
    return cm.value.code


def _build(work: Path, out: Path, monkeypatch, main=_main) -> str:
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "argv", [str(work / "project_driver.py")])
    obj = work / "build" / "dummy"
    args = ["build", "-e", str(work / "env.yml"), "-p", str(work / "design.yml"), "--obj_dir", str(obj)]
    assert main(str(work / "project_driver.py"), args, out, monkeypatch) == 0
    return str(obj / "hammer_dag.py")


def _syn(dag_file: str, out: Path, monkeypatch, main=_main) -> int:
    """syn as the DAG's task runs it: the DAG's driver, from WORK_DIR, with its configs."""
    dag = design_share.read_dag(dag_file)
    monkeypatch.chdir(dag["WORK_DIR"])
    args = ["syn"] + [a for e in dag["ENV_CONFIGS"] for a in ("-e", e)] + \
        [a for p in dag["PROJ_CONFIGS"] for a in ("-p", p)] + ["--obj_dir", dag["OBJ_DIR"]]
    return main(dag["HAMMER_EXEC"], args, out, monkeypatch)


def _share_and_pull(db: FakeDB, dag_file: str, into: Path) -> dict:
    bundle = design_share.bundle_from_dag(dag_file, owner="alice")
    db.publish("dummy", bundle.manifest, bundle.data, bundle.bundle_sha)
    db.share("dummy", "bob")
    db.user = "bob"
    return design_share.pull("alice", "dummy", str(into), fetch=db.fetch)


@pytest.mark.parametrize("idiom", sorted(IDIOMS))
def test_the_bundle_holds_the_files_the_drivers_hooks_name(idiom, tmp_path, db, monkeypatch) -> None:
    alice = _alice_design(tmp_path, idiom)
    bundle = design_share.bundle_from_dag(_build(alice, tmp_path, monkeypatch), owner="alice")
    files = bundle.manifest["files"]
    assert bundle.manifest["driver"] == "work/project_driver.py"
    assert "work/scripts/syn_extra.tcl" in files
    assert files["work/scripts/syn_extra.tcl"] == design_share._sha256(str(alice / "scripts" / "syn_extra.tcl"))
    assert str(tmp_path / "site" / "common.tcl") in bundle.manifest["external"]


# A hook that names its file by concatenation inside a function keeps the file out of its fingerprint.
@pytest.mark.parametrize("idiom", sorted(set(IDIOMS) - {"concat"}))
def test_a_partner_restores_the_owners_syn_when_its_hooks_read_a_file(idiom, tmp_path, db, tool_runs,
                                                                       monkeypatch) -> None:
    alice = _alice_design(tmp_path, idiom)
    dag_file = _build(alice, tmp_path, monkeypatch)
    assert _syn(dag_file, tmp_path, monkeypatch) == 0
    assert tool_runs == [str(alice)] and len(db.blobs) == 1
    alices_keys = list(db.blobs)

    into = tmp_path / "bob" / "pulled"
    summary = _share_and_pull(db, dag_file, into)
    db.loads.clear()
    assert _syn(summary["dag_file"], tmp_path, monkeypatch) == 0
    assert db.loads == alices_keys
    assert tool_runs == [str(alice)]


@pytest.mark.parametrize("idiom", sorted(IDIOMS))
def test_the_pulled_hook_finds_its_file_when_the_partners_syn_runs(idiom, tmp_path, db, tool_runs,
                                                                   monkeypatch) -> None:
    alice = _alice_design(tmp_path, idiom)
    into = tmp_path / "bob" / "pulled"
    summary = _share_and_pull(db, _build(alice, tmp_path, monkeypatch), into)
    assert _syn(summary["dag_file"], tmp_path, monkeypatch) == 0
    assert tool_runs == [str(into)]
    assert (into / "build" / "dummy" / "syn-rundir" / "syn_extra.used").read_text() == TCL
