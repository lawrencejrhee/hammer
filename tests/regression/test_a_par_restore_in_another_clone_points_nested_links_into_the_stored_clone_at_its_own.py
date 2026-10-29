"""A par result restored into a partner's clone points every absolute link at any depth into the owner's build dir
or clone at the same entry of the partner's own, and leaves links elsewhere, relative links, links with '..' and the
bytes of every tool output and checkpoint file as stored, while both stages keep the owner's cache keys."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hammer.vlsi import CLIDriver, HammerDriver, pd_cache, pd_store


@pytest.fixture
def db(monkeypatch):
    state = SimpleNamespace(blobs={}, loads=[], runs=[])

    def store(stage, key, data, duration_seconds=None, cpu_seconds=None, **_):
        state.blobs[key] = (stage, data, duration_seconds, cpu_seconds)

    def load(key):
        state.loads.append(key)
        return state.blobs.get(key)

    monkeypatch.setattr(pd_store, "store_stage_blob", store)
    monkeypatch.setattr(pd_store, "load_stage_blob", load)
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    for name in ("HAMMER_AIRFLOW_DESIGN", "HAMMER_AIRFLOW_RUN_ID", "AIRFLOW_CTX_DAG_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    return state


def _clone(root: Path) -> Path:
    work = root / "teamproj"
    (work / "rtl").mkdir(parents=True)
    (work / "rtl" / "top.v").write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (work / "design.yml").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.build_system": "sledgehammer",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(work / "rtl" / "top.v")],
    }, indent=4))
    cache = work / "build" / "tech-sky130-cache"
    cache.mkdir(parents=True)
    (cache / "sky130_scl_9T.tlef").write_text("VERSION 5.8 ;\n")
    (cache / "sky130_ef_io.lef").write_text("VERSION 5.8 ;\n")
    return work


def _innovus_rundir(rundir: Path, library: Path) -> None:
    """What Innovus leaves beside the nop tool's output: checkpoint dbs whose libs are absolute links."""
    obj, work = rundir.parent, rundir.parent.parent
    db = rundir / "pre_route_opt_design"
    (db / "libs" / "lef").mkdir(parents=True)
    (db / "libs" / "mmmc").mkdir()
    (db / "viewDefinition.tcl").write_text(f"create_library_set -timing {obj}/tech-sky130-cache/x.lib\n")
    (db / "dummy.globals").write_text(f"set init_design_uniquify {rundir}/pre_route_opt_design\n")
    (db / "libs" / "lef" / "sky130_scl_9T.tlef").symlink_to(obj / "tech-sky130-cache" / "sky130_scl_9T.tlef")
    (db / "libs" / "lef" / "sky130_scl_9T.lef").symlink_to(library / "sky130_scl_9T.lef")
    (db / "libs" / "lef" / "tech.lef").symlink_to("sky130_scl_9T.tlef")
    (db / "libs" / "lef" / "dotdot.lef").symlink_to(f"{obj}/syn-rundir/../tech-sky130-cache/sky130_scl_9T.tlef")
    (db / "libs" / "mmmc" / "dummy.mapped.sdc").symlink_to(obj / "syn-rundir" / "dummy.mapped.sdc")
    (db / "libs" / "mmmc" / "clock.sdc").symlink_to(rundir / "clock_constraints_fragment.sdc")
    (db / "libs" / "top.v").symlink_to(work / "rtl" / "top.v")
    final = rundir / "post_write_design" / "libs" / "lef"
    final.mkdir(parents=True)
    (final / "sky130_ef_io.lef").symlink_to(obj / "tech-sky130-cache" / "sky130_ef_io.lef")
    (rundir / "post_route_opt_design").symlink_to(db)
    (rundir / "latest").symlink_to("post_write_design")
    (rundir / "clock_constraints_fragment.sdc").write_text("create_clock -period 10 clock\n")
    (rundir / "dummy.def").write_text(f"# written in {rundir}\n")
    (rundir / "par.tcl").write_text(f"read_mmmc {rundir}/mmmc.tcl\n")
    (rundir / "innovus.log").write_text(f"Sourcing {rundir}/par.tcl\n")
    (rundir / "generated-scripts").mkdir(exist_ok=True)
    (rundir / "generated-scripts" / "open_chip").write_text(f"cd {rundir}\nsource enter\n")
    (rundir / "enter").write_text(f"export PATH='{work}/.venv/bin:/usr/bin'\n")


def _run_syn_and_par(work: Path) -> None:
    obj, out = work / "build", work.parent / "out"
    out.mkdir(exist_ok=True)
    par_in = obj / "par-input.json"
    for args in (["syn", "-p", str(work / "design.yml")],
                 ["syn-to-par", "-p", str(obj / "syn-rundir" / "syn-output-full.json"), "-o", str(par_in)],
                 ["par", "-p", str(par_in)]):
        if "-o" not in args:
            args = args + ["-o", str(out / f"{args[0]}-out.json")]
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=args + ["--obj_dir", str(obj), "--log", str(out / "log.txt")])
        assert cm.value.code == 0, args[0]


def _links(root: Path) -> dict:
    out = {}
    for top, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(top) / name
            if path.is_symlink():
                out[path.relative_to(root).as_posix()] = os.readlink(path)
    return out


def _files(root: Path) -> dict:
    out = {}
    for top, _, files in os.walk(root):
        for name in files:
            path = Path(top) / name
            if not path.is_symlink():
                out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def test_nested_links_into_the_owners_build_dir_and_clone_follow_the_restore(tmp_path, db, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    (library / "sky130_scl_9T.lef").write_text("VERSION 5.8 ;\n")
    real_syn, real_par = HammerDriver.run_synthesis, HammerDriver.run_par

    def run_synthesis(self, *args, **kwargs):
        db.runs.append("syn")
        return real_syn(self, *args, **kwargs)

    def run_par(self, *args, **kwargs):
        db.runs.append("par")
        result = real_par(self, *args, **kwargs)
        _innovus_rundir(Path(self.par_tool.run_dir), library)
        return result

    monkeypatch.setattr(HammerDriver, "run_synthesis", run_synthesis)
    monkeypatch.setattr(HammerDriver, "run_par", run_par)

    work_a = _clone(tmp_path / "s12345")
    obj_a = work_a / "build"
    monkeypatch.chdir(work_a)
    _run_syn_and_par(work_a)
    assert db.runs == ["syn", "par"] and len(db.blobs) == 2
    keys = list(db.blobs)
    stored = tmp_path / "stored"
    pd_store.untar_to_directory(db.blobs[keys[1]][1], stored)

    work_b = _clone(tmp_path / "s12346")
    obj_b = work_b / "build"
    rundir_b = obj_b / "par-rundir"
    monkeypatch.chdir(work_b)
    db.loads.clear()
    _run_syn_and_par(work_b)

    assert db.loads == keys and db.runs == ["syn", "par"]
    db_dir = "pre_route_opt_design/libs"
    assert _links(rundir_b) == {
        "latest": "post_write_design",
        "post_route_opt_design": "pre_route_opt_design",
        f"{db_dir}/lef/sky130_scl_9T.tlef": f"{obj_b}/tech-sky130-cache/sky130_scl_9T.tlef",
        f"{db_dir}/lef/sky130_scl_9T.lef": f"{library}/sky130_scl_9T.lef",
        f"{db_dir}/lef/tech.lef": "sky130_scl_9T.tlef",
        f"{db_dir}/lef/dotdot.lef": f"{obj_a}/syn-rundir/../tech-sky130-cache/sky130_scl_9T.tlef",
        f"{db_dir}/mmmc/dummy.mapped.sdc": f"{obj_b}/syn-rundir/dummy.mapped.sdc",
        f"{db_dir}/mmmc/clock.sdc": "../../../clock_constraints_fragment.sdc",
        f"{db_dir}/top.v": f"{work_b}/rtl/top.v",
        "post_write_design/libs/lef/sky130_ef_io.lef": f"{obj_b}/tech-sky130-cache/sky130_ef_io.lef",
    }
    assert (rundir_b / db_dir / "mmmc" / "clock.sdc").read_text() == "create_clock -period 10 clock\n"
    assert f"cd {rundir_b}\n" in (rundir_b / "generated-scripts" / "open_chip").read_text()

    restored = _files(rundir_b)
    as_stored = {rel: data for rel, data in _files(stored / "par-rundir").items()
                 if rel != "enter" and not rel.startswith("generated-scripts/")
                 and not ("/" not in rel and rel.endswith(".json"))}
    assert {"pre_route_opt_design/viewDefinition.tcl", "pre_route_opt_design/dummy.globals", "dummy.def",
            "par.tcl", "innovus.log"} <= set(as_stored)
    assert {rel: restored.get(rel) for rel in as_stored} == as_stored
