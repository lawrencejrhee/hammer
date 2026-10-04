import json
from types import SimpleNamespace

from hammer.vlsi import fingerprints as fp
from hammer.vlsi import pd_cache


def _roots(obj):
    return fp.make_roots(str(obj / "tech-cache"), str(obj), hammer_root="")


def _file(path, data=b"x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _drc_db(obj):
    return {"vlsi.core.drc_tool": "hammer.drc.pegasus",
            "drc.inputs.layout_file": str(obj / "par-rundir" / "top.gds"),
            "drc.inputs.top_module": "top",
            "par.outputs.output_gds": str(obj / "par-rundir" / "top.gds")}


def test_upstream_layout_is_content_hashed_and_portable(tmp_path):
    out = {}
    for name in ("objA", "objB"):
        obj = tmp_path / name
        _file(obj / "par-rundir" / "top.gds", b"GDS v1")
        out[name] = fp.config_lines(_drc_db(obj), _roots(obj), "drc", own_rundir=str(obj / "drc-rundir"))
    a = out["objA"]
    assert a == out["objB"]
    assert a["vlsi"] == [] and a["drc"] == []
    assert len(a["drc.upstream"]) == 1 and a["drc.upstream"][0].startswith("<OBJ_DIR>/par-rundir/top.gds:sha256=")

    gds = tmp_path / "objB" / "par-rundir" / "top.gds"
    gds.write_bytes(b"GDS v1")
    db_b, roots_b = _drc_db(tmp_path / "objB"), _roots(tmp_path / "objB")
    assert fp.config_lines(db_b, roots_b, "drc")["drc.upstream"] == a["drc.upstream"]
    gds.write_bytes(b"GDS v2")
    assert fp.config_lines(db_b, roots_b, "drc")["drc.upstream"] != a["drc.upstream"]


def test_upstream_scope_takes_only_build_dir_files_of_own_inputs(tmp_path):
    obj = tmp_path / "obj"
    netlist = _file(obj / "syn-rundir" / "top.mapped.v")
    cache_lef = _file(obj / "tech-cache" / "cells.lef")
    user_sdc = _file(tmp_path / "user" / "extra.sdc")
    db = {"par.inputs.input_files": [str(netlist)],
          "par.inputs.post_synth_sdc": str(obj / "syn-rundir" / "top.mapped.sdc"),
          "par.inputs.tech_lef": str(cache_lef),
          "par.inputs.user_sdc": str(user_sdc),
          "par.innovus.floorplan_tcl": str(netlist)}
    out = fp.config_lines(db, _roots(obj), "par")
    assert out["par.upstream"] == sorted([fp.file_line(str(netlist), _roots(obj)),
                                          "MISSING:<OBJ_DIR>/syn-rundir/top.mapped.sdc"])
    assert out["par"] == sorted([fp.file_line(str(cache_lef), _roots(obj)), fp.file_line(str(user_sdc)),
                                 fp.file_line(str(netlist), _roots(obj))])
    assert fp.upstream_lines(db, _roots(obj), "par") == out["par.upstream"]


def test_files_in_the_stage_own_rundir_stay_out_of_its_scopes(tmp_path):
    obj = tmp_path / "obj"
    own = _file(obj / "drc-rundir" / "fill.gds")
    db = {"drc.inputs.extra_layouts": [str(own)], "drc.pegasus.waiver": str(own),
          "vlsi.inputs.extra": str(own)}
    out = fp.config_lines(db, _roots(obj), "drc", own_rundir=str(obj / "drc-rundir"))
    assert out["drc"] == [] and out["drc.upstream"] == []
    assert out["vlsi"] == [fp.file_line(str(own), _roots(obj))]


def test_rtl_stays_out_of_the_upstream_scope(tmp_path):
    obj = tmp_path / "obj"
    rtl = _file(obj / "gen" / "top.v", b"module top; endmodule\n")
    db = {"synthesis.inputs.input_files": [str(rtl)], "sim.inputs.input_files": [str(rtl)]}
    assert fp.upstream_lines(db, _roots(obj), "synthesis") == []
    assert fp.upstream_lines(db, _roots(obj), "sim") == []


def test_outputs_named_files_do_not_count(tmp_path):
    obj = tmp_path / "obj"
    _file(obj / "par-rundir" / "top.gds", b"GDS v1")
    db = _drc_db(obj)
    db["par.outputs.output_netlist"] = str(_file(obj / "par-rundir" / "top.v"))
    before = fp.config_lines(db, _roots(obj))
    (obj / "par-rundir" / "top.v").write_bytes(b"module changed; endmodule\n")
    assert fp.config_lines(db, _roots(obj)) == before


def _ilm_db(obj):
    ilm_dir = obj / "par-Child" / "ChildILMDir"
    return {"vlsi.inputs.ilms": [{
        "dir": str(ilm_dir), "data_dir": str(ilm_dir / "mmmc" / "ilm_data" / "Child"), "module": "Child",
        "lef": str(obj / "par-Child" / "Child.lef"), "gds": str(obj / "par-Child" / "Child.gds"),
        "netlist": str(obj / "par-Child" / "Child.v"), "sdcs": [str(obj / "par-Child" / "Child.sdc")]}]}


def _make_ilm(obj, timing=b"timing v1"):
    for name in ("Child.lef", "Child.gds", "Child.v", "Child.sdc"):
        _file(obj / "par-Child" / name, name.encode())
    _file(obj / "par-Child" / "ChildILMDir" / "mmmc" / "ilm_data" / "Child" / "Child.timing", timing)
    _file(obj / "par-Child" / "ChildILMDir" / "Child.ilm.v", b"ilm netlist")


def test_ilms_are_global_walked_and_portable(tmp_path):
    for name in ("objA", "objB"):
        _make_ilm(tmp_path / name)
    a = fp.config_lines(_ilm_db(tmp_path / "objA"), _roots(tmp_path / "objA"), "par")
    b = fp.config_lines(_ilm_db(tmp_path / "objB"), _roots(tmp_path / "objB"), "par")
    assert a == b
    assert a["par"] == [] and a["par.upstream"] == []
    assert sorted(line.split(":")[0] for line in a["vlsi"]) == [
        "<OBJ_DIR>/par-Child/Child.gds", "<OBJ_DIR>/par-Child/Child.lef", "<OBJ_DIR>/par-Child/Child.sdc",
        "<OBJ_DIR>/par-Child/Child.v", "<OBJ_DIR>/par-Child/ChildILMDir/Child.ilm.v",
        "<OBJ_DIR>/par-Child/ChildILMDir/mmmc/ilm_data/Child/Child.timing"]
    _make_ilm(tmp_path / "objB", timing=b"timing v2")
    assert fp.config_lines(_ilm_db(tmp_path / "objB"), _roots(tmp_path / "objB"), "par")["vlsi"] != a["vlsi"]


def test_ilm_walk_is_capped(tmp_path, monkeypatch):
    obj = tmp_path / "obj"
    _make_ilm(obj)
    monkeypatch.setattr(fp, "DIR_WALK_CAP", 1)
    walked = [line for line in fp.config_lines(_ilm_db(obj), _roots(obj))["vlsi"] if "ChildILMDir" in line]
    assert len(walked) == 1 and walked[0].startswith("DIRSTAT:<OBJ_DIR>/par-Child/ChildILMDir:2:")


def test_build_dir_macros_and_sdcs_are_global_by_owner(tmp_path):
    obj = tmp_path / "obj"
    lef = _file(obj / "macros" / "sram.lef", b"MACRO sram\n")
    spice = _file(obj / "macros" / "sram.spice", b".subckt sram\n")
    sdc = _file(obj / "constraints" / "io.sdc", b"set_input_delay 1\n")
    db = {"vlsi.technology.extra_libraries": [{"library": {"lef_file": str(lef), "spice_file": str(spice)}}],
          "vlsi.inputs.custom_sdc_files": [str(sdc)]}
    lvs = fp.config_lines(db, _roots(obj), "lvs")
    assert lvs["vlsi"] == sorted([fp.file_line(str(lef), _roots(obj)), fp.file_line(str(sdc), _roots(obj))])
    assert lvs["lvs"] == [fp.file_line(str(spice), _roots(obj))]
    assert lvs["vlsi"][0].startswith("<OBJ_DIR>/")


def _driver(obj, db, **extra):
    return SimpleNamespace(database=SimpleNamespace(get_database_json=lambda: json.dumps(db)),
                           obj_dir=str(obj), **extra)


def _par_db(obj):
    return {"par.inputs.input_files": [str(obj / "syn-rundir" / "top.mapped.v")],
            "par.inputs.top_module": "top", "vlsi.rtl_fingerprint_sha256": "fixed"}


def test_cache_key_input_fingerprint_is_the_upstream_digest(tmp_path):
    keys = []
    for name in ("objA", "objB"):
        obj = tmp_path / name
        _file(obj / "syn-rundir" / "top.mapped.v", b"module top; endmodule\n")
        driver = _driver(obj, _par_db(obj))
        assert pd_cache._stage_input_fingerprint(driver, _par_db(obj), "par") == fp.digest_lines(
            fp.upstream_lines(_par_db(obj), fp.path_roots(driver), "par"))
        keys.append(pd_cache._build_cache_key(driver, "par"))
    assert keys[0] == keys[1]
    (tmp_path / "objB" / "syn-rundir" / "top.mapped.v").write_bytes(b"module top(); endmodule\n")
    assert pd_cache._build_cache_key(_driver(tmp_path / "objB", _par_db(tmp_path / "objB")), "par") != keys[0]


def test_cache_key_leaves_build_dir_rtl_to_the_rtl_fingerprint(tmp_path):
    obj = tmp_path / "obj"
    rtl = _file(obj / "gen" / "top.v", b"module top; endmodule\n")
    db = {"synthesis.inputs.input_files": [str(rtl)], "synthesis.inputs.top_module": "top",
          "vlsi.rtl_fingerprint_sha256": "fixed"}
    before = pd_cache._build_cache_key(_driver(obj, db), "synthesis")
    rtl.write_bytes(b"module top; /* comment */ endmodule\n")
    assert pd_cache._build_cache_key(_driver(obj, db), "synthesis") == before


def test_stage_rundir_inputs_reach_the_cache_key_by_content_only(tmp_path):
    obj = tmp_path / "obj"
    own = _file(obj / "par-rundir" / "pre_place.tcl")
    db = dict(_par_db(obj), **{"par.inputs.extra": str(own)})
    _file(obj / "syn-rundir" / "top.mapped.v")
    driver = _driver(obj, db, par_tool=SimpleNamespace(run_dir=str(obj / "par-rundir")))
    inputs = pd_cache._stage_input_fingerprint(driver, db, "par")
    before = pd_cache._build_cache_key(driver, "par")
    own.write_bytes(b"rewritten\n")
    assert pd_cache._stage_input_fingerprint(driver, db, "par") == inputs
    assert pd_cache._build_cache_key(driver, "par") != before


def test_action_memo_hashes_each_input_once(tmp_path, monkeypatch):
    obj = tmp_path / "obj"
    netlist = _file(obj / "syn-rundir" / "top.mapped.v", b"module top; endmodule\n")
    hashed = []
    real = fp.sha256_file
    monkeypatch.setattr(fp, "sha256_file", lambda p: hashed.append(p) or real(p))
    driver = _driver(obj, _par_db(obj))
    memo = fp.begin_action_memo(driver)
    lines = fp.config_lines(_par_db(obj), fp.path_roots(driver), "par", memo=memo)
    first = pd_cache._build_cache_key(driver, "par")
    assert pd_cache._build_cache_key(driver, "par") == first
    assert hashed == [str(netlist)]
    assert lines["par.upstream"] == fp.upstream_lines(_par_db(obj), fp.path_roots(driver), "par", memo=memo)

    netlist.write_bytes(b"module top(); endmodule\n")
    fp.begin_action_memo(driver)
    assert pd_cache._build_cache_key(driver, "par") != first
    assert len(hashed) == 2
    assert fp.action_memo(SimpleNamespace()) is None
