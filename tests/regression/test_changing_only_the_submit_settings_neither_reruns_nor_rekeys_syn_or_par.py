import hashlib
import json
import os
from pathlib import Path

import pytest

from hammer.vlsi import CLIDriver, HammerDriver, pd_cache, pd_store, rtl_check

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
def db(monkeypatch):
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
            runs.append(_name)
            return _real(self, *args, **kwargs)
        monkeypatch.setattr(HammerDriver, name, spy)
    return runs


def _srun(path: Path) -> Path:
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\nexec \"$@\"\n")
    os.chmod(path, 0o755)
    return path


def _course_yml(path: Path, srun: Path, mem: str = "6G", time: str = "02:00:00") -> Path:
    blocks = [SUBMIT]
    for stage in ("syn", "par", "drc", "lvs"):
        blocks.append(f'course.slurm.{stage}:\n  - slurm:\n      srun_binary: "{srun}"\n      partition: "eda"\n'
                      f'      extra_args:\n        - "--ntasks=1"\n        - "--mem={mem}"\n'
                      f'        - "--time={time}"\n        - "--job-name=h-{stage}"\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks))
    return path


def _design(work: Path, period: str) -> None:
    (work / "design.yml").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "vlsi.technology.extra_libraries": [{"library": {"lef_file": str(work / "lef" / "macro.lef")}}],
        "vlsi.inputs.clocks": [{"name": "clock", "period": period}],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(work / "src" / "top.v")],
        "synthesis.mocksynth.temp_folder": str(work / "mock"),
    }, indent=4))


def _clone(home: Path) -> Path:
    work = home / "teamproj"
    for sub in ("src", "lef", "mock"):
        (work / sub).mkdir(parents=True)
    (work / "src" / "top.v").write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (work / "lef" / "macro.lef").write_text("MACRO macro_a\nEND macro_a\n")
    _design(work, "10ns")
    return work


def _hammer(args, log: Path, capsys) -> str:
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=args + ["--log", str(log)])
    out = capsys.readouterr().out
    assert cm.value.code == 0, out
    return out


def _syn_and_par(work: Path, course: Path, build: str, capsys) -> str:
    obj = work / build
    old = os.getcwd()
    os.chdir(work)
    capsys.readouterr()
    try:
        out = _hammer(["syn", "-p", str(work / "design.yml"), "-p", str(course), "--obj_dir", str(obj),
                       "-o", str(work / "syn-out.json")], work / "log.txt", capsys)
        _hammer(["syn-to-par", "-p", str(obj / "syn-rundir" / "syn-output-full.json"),
                 "-o", str(obj / "par-input.json"), "--obj_dir", str(obj)], work / "log.txt", capsys)
        out += _hammer(["par", "-p", str(obj / "par-input.json"), "--obj_dir", str(obj),
                        "-o", str(work / "par-out.json")], work / "log.txt", capsys)
    finally:
        os.chdir(old)
    return out


def test_changing_only_the_submit_settings_neither_reruns_nor_rekeys_syn_or_par(tmp_path, db, tool_runs,
                                                                               capsys) -> None:
    course = tmp_path / "tools" / "course" / "project" / "sledgehammer.yml"
    srun = _srun(tmp_path / "usr" / "bin" / "srun")
    work = _clone(tmp_path / "s12345")
    _course_yml(course, srun)
    _syn_and_par(work, course, "build", capsys)
    keys = list(db.blobs)
    assert len(keys) == 2 and tool_runs == ["run_synthesis", "run_par"]

    moved = _srun(tmp_path / "opt" / "slurm" / "bin" / "srun")
    for fresh, new_srun in (("build-mem", srun), ("build-srun", moved)):
        _course_yml(course, new_srun, mem="16G", time="04:00:00")
        out = _syn_and_par(work, course, "build", capsys)
        assert "can skip syn" in out and "can skip par" in out
        db.loads.clear()
        _syn_and_par(work, course, fresh, capsys)
        assert db.loads == keys and len(tool_runs) == 2

    _design(work, "5ns")
    db.loads.clear()
    out = _syn_and_par(work, course, "build", capsys)
    assert "can skip syn" not in out and "can skip par" not in out
    assert len(db.loads) == 2 and not set(db.loads) & set(keys) and len(tool_runs) == 4
    keys += db.loads

    (work / "lef" / "macro.lef").write_text("MACRO macro_a\nEND macro_a\nMACRO macro_b\nEND macro_b\n")
    db.loads.clear()
    out = _syn_and_par(work, course, "build", capsys)
    assert "can skip syn" not in out and "can skip par" not in out
    assert len(db.loads) == 2 and not set(db.loads) & set(keys) and len(tool_runs) == 6
