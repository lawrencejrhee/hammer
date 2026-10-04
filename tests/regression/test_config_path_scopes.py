import errno
import os

import pytest

from hammer.config.config_src import RUN_CONTROL_KEYS, StageGraph
from hammer.vlsi import fingerprints as fp


def _roots(tmp_path):
    return fp.make_roots(str(tmp_path / "obj" / "tech-cache"), str(tmp_path / "obj"), hammer_root="")


def _file(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _line(path):
    return fp.file_line(str(path))


def test_sdc_source_inside_a_tcl_string_is_global(tmp_path):
    clocks = _file(tmp_path / "user" / "clocks.tcl", "create_clock -period 2 clk\n")
    db = {"vlsi.inputs.custom_sdc_constraints": [
        f"source {clocks}",
        "set_load 1 [get_ports /VSS]",
        "set_false_path -through {/designs/top/u0}",
    ]}
    out = fp.config_lines(db, _roots(tmp_path), "par")
    assert out == {"vlsi": [_line(clocks)], "par": [], "par.upstream": []}
    before = fp.digest_lines(out["vlsi"])
    clocks.write_text("create_clock -period 2.5 clk\n")
    assert fp.digest_lines(fp.config_lines(db, _roots(tmp_path), "par")["vlsi"]) != before


def test_deck_include_in_additional_drc_text_reaches_drc_only(tmp_path):
    deck = _file(tmp_path / "pdk" / "waivers.pvl")
    db = {"vlsi.core.drc_tool": "hammer.drc.pegasus",
          "drc.inputs.additional_drc_text": f'include "{deck}"\nRULE x {{ }}\n'}
    drc = fp.config_lines(db, _roots(tmp_path), "drc")
    assert drc == {"vlsi": [], "drc": [_line(deck)], "drc.upstream": []}
    assert all(not lines for lines in fp.config_lines(db, _roots(tmp_path), "par").values())


def test_unselected_tool_keys_give_no_file_lines(tmp_path):
    innovus = _file(tmp_path / "u" / "innovus.tcl")
    openroad = _file(tmp_path / "u" / "openroad.tcl")
    yosys = _file(tmp_path / "u" / "yosys.lib")
    db = {"vlsi.core.par_tool": "hammer.par.innovus",
          "vlsi.core.synthesis_tool": "hammer.synthesis.genus",
          "par.innovus.extra_tcl": str(innovus),
          "par.openroad.setrc_file": str(openroad),
          "synthesis.yosys.liberty": str(yosys)}
    assert {"innovus", "openroad"} <= set(fp.tool_packages("par"))
    assert "par.openroad." in fp.unselected_tool_prefixes(db, "par")
    assert "par.innovus." not in fp.unselected_tool_prefixes(db, "par")
    assert fp.config_lines(db, _roots(tmp_path), "par")["par"] == [_line(innovus)]
    assert fp.config_lines(db, _roots(tmp_path), "synthesis")["synthesis"] == []
    everything = fp.config_lines(db, _roots(tmp_path))
    assert everything["par"] == sorted([_line(innovus), _line(openroad)])
    assert everything["synthesis"] == [_line(yosys)]


def test_tool_plugins_are_found_in_every_namespace_portion(tmp_path, monkeypatch):
    _file(tmp_path / "portion" / "hammer" / "par" / "fancy" / "__init__.py", "")
    monkeypatch.syspath_prepend(str(tmp_path / "portion"))
    assert {"fancy", "innovus"} <= set(fp.tool_packages("par"))
    assert "par.fancy." in fp.unselected_tool_prefixes({"vlsi.core.par_tool": "hammer.par.innovus"}, "par")


def test_spice_fields_go_to_lvs_only_including_nested_paths(tmp_path):
    lef = _file(tmp_path / "pdk" / "macro.lef")
    cdl = _file(tmp_path / "pdk" / "macro.cdl")
    top_spice = _file(tmp_path / "pdk" / "top.sp")
    models = tmp_path / "pdk" / "models.spice"
    db = {"vlsi.technology.extra_libraries": [{"library": {
              "lef_file": str(lef),
              "spice_file": str(cdl),
              "spice_model_file": {"path": str(models), "lib_corner": "tt"}}}],
          "technology.extra.spice_file": str(top_spice)}
    lvs = fp.config_lines(db, _roots(tmp_path), "lvs")
    assert lvs["vlsi"] == [_line(lef)]
    assert lvs["lvs"] == sorted([_line(cdl), _line(top_spice), f"MISSING:{os.path.normpath(str(models))}"])
    par = fp.config_lines(db, _roots(tmp_path), "par")
    assert par == {"vlsi": [_line(lef)], "par": [], "par.upstream": []}


def test_owner_is_the_dotted_stage_prefix_else_global(tmp_path):
    files = {k: _file(tmp_path / "u" / f"{i}.tcl") for i, k in enumerate(
        ["parasitics.file", "sram_generator.extra", "cadence.setup", "technology.x.io_file",
         "sim.inputs.extra", "power.voltus.extra"])}
    out = fp.config_lines({k: str(v) for k, v in files.items()}, _roots(tmp_path))
    assert out["vlsi"] == sorted(_line(files[k]) for k in
                                 ("parasitics.file", "sram_generator.extra", "cadence.setup", "technology.x.io_file"))
    assert out["sim"] == [_line(files["sim.inputs.extra"])]
    assert out["power"] == [_line(files["power.voltus.extra"])]
    assert fp.owner("par") == "vlsi" and fp.owner("parx.y") == "vlsi" and fp.owner("par.x") == "par"
    with pytest.raises(ValueError):
        fp.config_lines({}, _roots(tmp_path), "sram_generator")


def test_owned_tags_match_the_stage_graph():
    assert fp.OWNED_STAGE_TAGS == StageGraph().stageTagTuple


def test_run_control_keys_are_skipped_by_exact_membership(tmp_path):
    f = _file(tmp_path / "u" / "rules.tcl")
    assert all(not v for v in fp.config_lines({k: str(f) for k in RUN_CONTROL_KEYS}, _roots(tmp_path)).values())
    out = fp.config_lines({"vlsi.pd_cache.custom_rules": str(f)}, _roots(tmp_path))
    assert out["vlsi"] == [_line(f)]


def test_outputs_bookkeeping_and_fingerprint_keys_are_skipped(tmp_path):
    f = _file(tmp_path / "u" / "x.gds")
    db = {"par.outputs.output_gds": str(f), "drc.needsToRerun": str(f),
          "vlsi.collateral_fingerprint_sha256": str(f), "lvs.hooks_fingerprint_sha256": str(f)}
    assert all(not v for v in fp.config_lines(db, _roots(tmp_path)).values())


def test_missing_paths_count_only_with_a_collateral_suffix(tmp_path):
    later = tmp_path / "u" / "later.tcl"
    db = {"vlsi.inputs.extra": [str(tmp_path / "u" / "nope"), str(later), "/VSS", "/designs/top"]}
    first = fp.config_lines(db, _roots(tmp_path))["vlsi"]
    assert first == [f"MISSING:{os.path.normpath(str(later))}"]
    assert fp.config_lines(db, _roots(tmp_path))["vlsi"] == first
    _file(later)
    assert fp.config_lines(db, _roots(tmp_path))["vlsi"] == [_line(later)]


def test_existing_files_count_whatever_their_extension_but_dirs_do_not(tmp_path):
    deck = _file(tmp_path / "pdk" / "calibre_drc_rules")
    (tmp_path / "pdk" / "rules.d").mkdir()
    db = {"technology.x.drc_deck": str(deck), "technology.x.rules_dir": str(tmp_path / "pdk" / "rules.d")}
    assert fp.config_lines(db, _roots(tmp_path))["vlsi"] == [_line(deck)]


def test_rtl_is_left_to_the_rtl_fingerprint(tmp_path):
    rtl = _file(tmp_path / "src" / "top.v", "module top; endmodule\n")
    db = {"synthesis.inputs.input_files": [str(rtl)], "sim.inputs.input_files": [str(rtl)]}
    assert all(not v for v in fp.config_lines(db, _roots(tmp_path)).values())
    other = _file(tmp_path / "src" / "tb.v")
    db["sim.inputs.input_files"].append(str(other))
    assert fp.config_lines(db, _roots(tmp_path))["sim"] == [_line(other)]
    assert fp.config_lines(db, _roots(tmp_path), rtl_set=[str(other)])["sim"] == [_line(rtl)]


def test_token_stat_errors_are_never_fatal(tmp_path, monkeypatch):
    def _eio(path, roots):
        raise OSError(errno.EIO, "I/O error", path)
    monkeypatch.setattr(fp, "_file_line", _eio)
    tcl = tmp_path / "u" / "x.tcl"
    db = {"vlsi.inputs.a": f"source {tcl}", "vlsi.inputs.b": str(tmp_path / "u" / "plain")}
    assert fp.config_lines(db, _roots(tmp_path))["vlsi"] == [f"UNREADABLE:{os.path.normpath(str(tcl))}"]


def test_global_lines_do_not_depend_on_the_stage(tmp_path):
    obj = tmp_path / "obj"
    db = {"vlsi.inputs.custom_sdc_files": [str(_file(obj / "par-rundir" / "extra.sdc"))],
          "technology.x.io_file": str(_file(tmp_path / "u" / "chip.io")),
          "vlsi.technology.extra_libraries": [{"library": {"lef_file": str(_file(obj / "m" / "m.lef")),
                                                           "spice_file": str(_file(obj / "m" / "m.sp"))}}],
          "par.inputs.input_files": [str(_file(obj / "syn-rundir" / "top.mapped.v"))]}
    expected = fp.config_lines(db, _roots(tmp_path))["vlsi"]
    assert len(expected) == 3
    for tag in ("synthesis", "par", "drc", "lvs"):
        rundir = str(obj / ("syn-rundir" if tag == "synthesis" else f"{tag}-rundir"))
        assert fp.config_lines(db, _roots(tmp_path), tag, own_rundir=rundir)["vlsi"] == expected


def test_include_dirs_are_walked_for_a_byte_rtl_hash(tmp_path):
    inc = tmp_path / "inc"
    header = _file(inc / "defs.svh", "`define W 4\n")
    _file(inc / "notes.md")
    out = fp.config_lines({}, _roots(tmp_path), "synthesis", rtl_include_dirs=[str(inc)])
    assert out["vlsi"] == [_line(header)]


def test_a_missing_spice_file_counts_until_it_appears(tmp_path):
    models = tmp_path / "pdk" / "models.spice"
    db = {"technology.x.models": str(models)}
    assert ".spice" in fp.collateral_suffixes()
    assert fp.config_lines(db, _roots(tmp_path), "lvs")["vlsi"] == [f"MISSING:{os.path.normpath(models)}"]
    _file(models, ".model n nmos\n")
    assert fp.config_lines(db, _roots(tmp_path), "lvs")["vlsi"] == [_line(models)]
