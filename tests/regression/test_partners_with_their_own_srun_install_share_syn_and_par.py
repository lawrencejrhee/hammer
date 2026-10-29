import hashlib
import json
import os
from pathlib import Path

import pytest

from hammer.vlsi import CLIDriver, HammerDriver, fingerprints, pd_cache, pd_store, rtl_check

SUBMIT = """\
vlsi.submit.command: "local"
synthesis.submit.command: "slurm"
synthesis.submit.settings: "course.slurm.syn"
par.submit.command: "slurm"
par.submit.settings: "course.slurm.par"
drc.submit.command: "slurm"
drc.submit.settings: "course.slurm.drc"
lvs.submit.command: "slurm"
lvs.submit.settings: "course.slurm.lvs"
sim.submit.command: "local"
"""


def _content_digest(paths, include_dirs=(), defines=(), top_module=None):
    data = b"".join(Path(p).read_bytes() for p in sorted(paths))
    return hashlib.sha256(data).hexdigest(), []


class FakeDB:
    def __init__(self) -> None:
        self.blobs: dict = {}
        self.loads: list = []

    def store_stage_blob(self, stage, key, data, duration_seconds=None, cpu_seconds=None, **_):
        self.blobs[key] = (stage, data, duration_seconds, cpu_seconds)

    def load_stage_blob(self, key):
        self.loads.append(key)
        return self.blobs.get(key)


@pytest.fixture
def db(monkeypatch, tmp_path):
    fake = FakeDB()
    monkeypatch.setattr(pd_store, "store_stage_blob", fake.store_stage_blob)
    monkeypatch.setattr(pd_store, "load_stage_blob", fake.load_stage_blob)
    monkeypatch.setattr(pd_store, "design_has_shares", lambda design: False)
    monkeypatch.setattr(pd_cache, "_record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(pd_cache, "_stamp_project_from_config", lambda *a, **k: None)
    monkeypatch.setattr(rtl_check, "digest_units", _content_digest)
    monkeypatch.setattr(rtl_check, "digest_files", _content_digest)
    monkeypatch.setenv("HAMMER_PD_CACHE", "1")
    monkeypatch.setenv("HAMMER_PD_CACHE_LEDGER", "0")
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    for name in ("HAMMER_AIRFLOW_DESIGN", "HAMMER_AIRFLOW_RUN_ID", "AIRFLOW_CTX_DAG_RUN_ID", "HAMMER_SUBSTEP_RESUME",
                 "HAMMER_PD_COLLAT_DEBUG"):
        monkeypatch.delenv(name, raising=False)
    return fake


@pytest.fixture
def tool_runs(monkeypatch):
    runs = []
    for name in ("run_synthesis", "run_par"):
        real = getattr(HammerDriver, name)

        def spy(self, *args, _real=real, _name=name, **kwargs):
            runs.append((_name, os.getcwd()))
            return _real(self, *args, **kwargs)
        monkeypatch.setattr(HammerDriver, name, spy)
    return runs


@pytest.fixture
def stage_fps(monkeypatch):
    seen = []
    real = fingerprints.stage_fingerprints

    def spy(driver, cli, stage_tag, *args, **kwargs):
        out = real(driver, cli, stage_tag, *args, **kwargs)
        seen.append((os.getcwd(), stage_tag, dict(out)))
        return out
    monkeypatch.setattr(fingerprints, "stage_fingerprints", spy)
    return seen


def _course_yml(path: Path, srun: Path) -> Path:
    blocks = [SUBMIT]
    for stage, mem in (("syn", "6G"), ("par", "12G"), ("drc", "8G"), ("lvs", "8G")):
        blocks.append(f'course.slurm.{stage}:\n  - slurm:\n      srun_binary: "{srun}"\n      partition: "eda"\n'
                      f'      extra_args:\n        - "--ntasks=1"\n        - "--mem={mem}"\n'
                      f'        - "--time=02:00:00"\n        - "--job-name=h-{stage}"\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks))
    return path


def _clone(home: Path) -> Path:
    work = home / "teamproj"
    (work / "src").mkdir(parents=True)
    (work / "mock").mkdir()
    (work / "src" / "top.v").write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (work / "design.yml").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "vlsi.inputs.clocks": [{"name": "clock", "period": "10ns"}],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(work / "src" / "top.v")],
        "synthesis.mocksynth.temp_folder": str(work / "mock"),
    }, indent=4))
    return work


def _hammer(args, log: Path) -> None:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=args + ["--log", str(log)])
    assert cm.value.code == 0, args[0]


def _syn_and_par(work: Path, course: Path) -> None:
    obj = work / "build"
    old = os.getcwd()
    os.chdir(work)
    try:
        _hammer(["syn", "-p", str(work / "design.yml"), "-p", str(course), "--obj_dir", str(obj),
                 "-o", str(work / "syn-out.json")], work / "log.txt")
        _hammer(["syn-to-par", "-p", str(obj / "syn-rundir" / "syn-output-full.json"),
                 "-o", str(obj / "par-input.json"), "--obj_dir", str(obj)], work / "log.txt")
        _hammer(["par", "-p", str(obj / "par-input.json"), "--obj_dir", str(obj),
                 "-o", str(work / "par-out.json")], work / "log.txt")
    finally:
        os.chdir(old)


def _reinstall(path: Path) -> None:
    """Replace path by a byte-identical copy with its mtime, as another VM's package install leaves it."""
    before = path.stat()
    new = path.with_name(path.name + ".rpmnew")
    new.write_bytes(path.read_bytes())
    os.chmod(new, before.st_mode)
    os.utime(new, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(new, path)
    after = path.stat()
    assert after.st_mtime_ns == before.st_mtime_ns and after.st_ctime_ns != before.st_ctime_ns


def test_partners_whose_vms_installed_srun_at_different_times_share_syn_and_par(tmp_path, db, tool_runs,
                                                                               stage_fps) -> None:
    srun = tmp_path / "usr" / "bin" / "srun"
    srun.parent.mkdir(parents=True)
    srun.write_text("#!/bin/sh\nexec \"$@\"\n")
    os.chmod(srun, 0o755)
    os.utime(srun, ns=(1_790_150_867_000_000_000, 1_790_150_867_000_000_000))
    course = _course_yml(tmp_path / "tools" / "course" / "project" / "sledgehammer.yml", srun)
    alice, bob = _clone(tmp_path / "s12345"), _clone(tmp_path / "s12346")

    _syn_and_par(alice, course)
    assert [name for name, _ in tool_runs] == ["run_synthesis", "run_par"] and len(db.blobs) == 2
    alices_keys = list(db.blobs)

    _reinstall(srun)
    db.loads.clear()
    _syn_and_par(bob, course)
    assert db.loads == alices_keys
    assert len(tool_runs) == 2 and len(db.blobs) == 2
    by_partner = {}
    for cwd, stage, fps in stage_fps:
        by_partner.setdefault(stage, {})[cwd] = fps
    for stage in ("synthesis", "par"):
        assert by_partner[stage][str(alice)] == by_partner[stage][str(bob)], stage
    assert str(alice) not in (bob / "build" / "par-rundir" / "par-output.json").read_text()
