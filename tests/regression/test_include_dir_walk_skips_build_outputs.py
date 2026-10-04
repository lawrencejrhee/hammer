import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, rtl_check
from hammer.vlsi import fingerprints as fp


def _file(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _roots(tmp_path: Path):
    return fp.make_roots(str(tmp_path / "obj" / "tech-cache"), str(tmp_path / "obj"), hammer_root="")


def test_the_walk_keeps_sources_and_headers_outside_build_outputs(tmp_path) -> None:
    kept = [_file(tmp_path / "defs.vh"), _file(tmp_path / "top.v"), _file(tmp_path / "pkg" / "types.svh")]
    for rel in ("obj/syn-rundir/top.mapped.v", "obj/tech-cache/cells.v", "obj/gen/inc.vh", "custom-rundir/net.v",
                "log.txt", "obj/master_database.json", "notes.md", "pkg/build.log"):
        _file(tmp_path / rel)
    assert fp.include_dir_lines([str(tmp_path)], _roots(tmp_path)) == sorted(fp.file_line(str(p)) for p in kept)


def test_an_include_dir_inside_the_build_dir_is_still_walked(tmp_path) -> None:
    roots = _roots(tmp_path)
    header = _file(tmp_path / "obj" / "gen" / "inc.vh")
    _file(tmp_path / "obj" / "gen" / "par-rundir" / "out.v")
    assert fp.include_dir_lines([str(tmp_path / "obj")], roots) == [fp.file_line(str(header), roots)]
    assert fp.include_dir_lines([str(tmp_path / "obj" / "gen")], roots) == [fp.file_line(str(header), roots)]


def test_a_relative_include_dir_is_taken_from_the_cwd(tmp_path, monkeypatch) -> None:
    header = _file(tmp_path / "inc" / "defs.vh")
    monkeypatch.chdir(tmp_path)
    assert fp.include_dir_lines(["inc"]) == [fp.file_line(str(header))]


def _no_slang(paths, include_dirs=(), defines=(), top_module=None):
    raise rtl_check.SlangNotFound("not installed")


@pytest.fixture
def fallback(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", _no_slang)
    for name in ("HAMMER_PD_CACHE", "HAMMER_AIRFLOW_DESIGN", "HAMMER_SUBSTEP_RESUME", "HAMMER_PD_COLLAT_DEBUG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


def _syn(tmp_path: Path, include_dirs) -> None:
    rtl = tmp_path / "top.v"
    if not rtl.exists():
        rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (tmp_path / "mock").mkdir(exist_ok=True)
    (tmp_path / "config.json").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(rtl)],
        "synthesis.inputs.include_dirs": include_dirs,
        "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
    }, cls=HammerJSONEncoder, indent=4))
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=["syn", "-p", str(tmp_path / "config.json"), "-o", str(tmp_path / "syn-out.json"),
                               "--obj_dir", str(tmp_path / "obj"), "--log", str(tmp_path / "log.txt")])
    assert cm.value.code == 0


def test_an_include_dir_holding_the_build_dir_does_not_rerun_syn_forever(tmp_path, fallback, capsys) -> None:
    header = _file(tmp_path / "defs.vh", "`define W 1\n")
    _syn(tmp_path, [str(tmp_path)])
    capsys.readouterr()
    for _ in range(3):
        _syn(tmp_path, [str(tmp_path)])
        assert "can skip syn" in capsys.readouterr().out
    header.write_text("`define W 2\n")
    _syn(tmp_path, [str(tmp_path)])
    assert "can skip syn" not in capsys.readouterr().out


def test_a_relative_include_dir_header_edit_reruns_syn(tmp_path, fallback, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    header = _file(tmp_path / "inc" / "defs.vh", "`define W 1\n")
    _syn(tmp_path, ["inc"])
    capsys.readouterr()
    _syn(tmp_path, ["inc"])
    assert "can skip syn" in capsys.readouterr().out
    header.write_text("`define W 2\n")
    _syn(tmp_path, ["inc"])
    assert "can skip syn" not in capsys.readouterr().out
