import os

import hammer.tech as hammer_tech
from hammer.vlsi import fingerprints as fp


def _file(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _tech(tmp_path, **config):
    tech = hammer_tech.HammerTechnology()
    tech.name = "t"
    tech.package = "hammer.technology.nop"
    tech.config = hammer_tech.TechConfig(name="t", **config)
    tech.cache_dir = str(tmp_path / "obj" / "tech-t-cache")
    return tech


def _roots(tmp_path):
    return fp.make_roots(str(tmp_path / "obj" / "tech-t-cache"), str(tmp_path / "obj"), hammer_root="")


def _line(path, tmp_path):
    return fp.file_line(str(path), _roots(tmp_path))


def _scopes(tmp_path, tech, db, stage):
    roots = _roots(tmp_path)
    return fp.merge_scopes(fp.config_lines(db, roots, stage), fp.deck_lines(tech, db, stage, roots),
                           fp.reader_lines(db, stage, roots))


def _deck_dir(tmp_path):
    d = tmp_path / "deck"
    files = {name: _file(d / name) for name in
             ("deck.pvl", "chip.io", "extra.sdc", "hammer-vlsi-x.log", "output.json", ".x.swp", "x~")}
    _file(d / "sub" / "inc.pvl")
    return files


def test_deck_siblings_never_narrow_other_files(tmp_path):
    files = _deck_dir(tmp_path)
    tech = _tech(tmp_path, drc_decks=[
        hammer_tech.DRCDeck(tool_name="pegasus", deck_name="drc", path=str(files["deck.pvl"])),
        hammer_tech.DRCDeck(tool_name="calibre", deck_name="drc", path=str(tmp_path / "calibre" / "rules"))])
    db = {"vlsi.core.drc_tool": "hammer.drc.pegasus", "vlsi.core.lvs_tool": "hammer.lvs.pegasus",
          "technology.t.drc_deck": str(files["deck.pvl"]), "technology.t.io_file": str(files["chip.io"]),
          "vlsi.inputs.custom_sdc_files": [str(files["extra.sdc"])]}
    drc = _scopes(tmp_path, tech, db, "drc")
    io_sdc = sorted([_line(files["chip.io"], tmp_path), _line(files["extra.sdc"], tmp_path)])
    assert drc["vlsi"] == io_sdc
    assert drc["drc"] == sorted(io_sdc + [_line(files["deck.pvl"], tmp_path)])
    syn = _scopes(tmp_path, tech, db, "synthesis")
    assert syn["vlsi"] == drc["vlsi"] and syn["synthesis"] == []
    assert fp.deck_lines(tech, db, "lvs", _roots(tmp_path))["lvs"] == []

    for name in ("hammer-vlsi-x.log", "output.json", ".x.swp", "x~"):
        files[name].write_text("touched\n")
    _file(tmp_path / "deck" / "sub" / "inc.pvl", "changed\n")
    assert _scopes(tmp_path, tech, db, "drc") == drc
    files["deck.pvl"].write_text("RULE new\n")
    after = _scopes(tmp_path, tech, db, "drc")
    assert after["vlsi"] == drc["vlsi"] and after["drc"] != drc["drc"]
    files["chip.io"].write_text("io changed\n")
    assert _scopes(tmp_path, tech, db, "synthesis")["vlsi"] != syn["vlsi"]


def test_narrowing_is_the_same_in_every_action(tmp_path):
    deck = _file(tmp_path / "pdk" / "lvs.rules")
    tech = _tech(tmp_path, lvs_decks=[hammer_tech.LVSDeck(tool_name="pegasus", deck_name="lvs", path=str(deck))])
    db = {"vlsi.core.lvs_tool": "hammer.lvs.pegasus", "technology.t.lvs_deck": str(deck)}
    narrowed = {stage: fp.deck_lines(tech, db, stage, _roots(tmp_path))[fp.NARROWED_SCOPE]
                for stage in fp.OWNED_STAGE_TAGS}
    assert set(map(tuple, narrowed.values())) == {(_line(deck, tmp_path),)}
    assert fp.deck_lines(tech, db, "lvs", _roots(tmp_path))["lvs"] == [_line(deck, tmp_path)]


def test_no_siblings_in_the_tech_cache_root_or_build_dir(tmp_path):
    cache_deck = _file(tmp_path / "obj" / "tech-t-cache" / "drc.rules")
    _file(tmp_path / "obj" / "tech-t-cache" / "tech_pgv.lib.tmp.12.3")
    _file(tmp_path / "obj" / "tech-t-cache" / "stdcells.txt")
    obj_deck = _file(tmp_path / "obj" / "lvs.rules")
    _file(tmp_path / "obj" / "syn-output.v")
    nested = _file(tmp_path / "obj" / "tech-t-cache" / "decks" / "lvs2.rules")
    sibling = _file(tmp_path / "obj" / "tech-t-cache" / "decks" / "include.rules")
    tech = _tech(tmp_path,
                 drc_decks=[hammer_tech.DRCDeck(tool_name="pegasus", deck_name="drc", path="cache/drc.rules")],
                 lvs_decks=[hammer_tech.LVSDeck(tool_name="pegasus", deck_name="lvs", path=str(obj_deck)),
                            hammer_tech.LVSDeck(tool_name="pegasus", deck_name="lvs2", path=str(nested))])
    db = {"vlsi.core.drc_tool": "hammer.drc.pegasus", "vlsi.core.lvs_tool": "hammer.lvs.pegasus"}
    out = fp.deck_lines(tech, db, None, _roots(tmp_path))
    assert out["drc"] == [_line(cache_deck, tmp_path)]
    assert out["lvs"] == sorted([_line(obj_deck, tmp_path), _line(nested, tmp_path), _line(sibling, tmp_path)])
    assert out["drc"][0].startswith("<TECH_CACHE>/drc.rules:sha256=")
    assert tech.config.drc_decks[0].path == "cache/drc.rules"


def test_netgen_reads_the_drc_deck_only_without_a_magic_rcfile(tmp_path):
    techfile = _file(tmp_path / "pdk" / "magic" / "t.tech")
    rcfile = _file(tmp_path / "pdk" / "magic" / "t.magicrc")
    tech = _tech(tmp_path, drc_decks=[hammer_tech.DRCDeck(tool_name="magic", deck_name="m", path=str(techfile))])
    db = {"vlsi.core.drc_tool": "hammer.drc.magic", "vlsi.core.lvs_tool": "hammer.lvs.netgen"}
    both = sorted([_line(techfile, tmp_path), _line(rcfile, tmp_path)])
    assert fp.deck_lines(tech, db, "lvs", _roots(tmp_path))["lvs"] == both
    db["drc.magic.rcfile"] = str(rcfile)
    assert fp.deck_lines(tech, db, "lvs", _roots(tmp_path))["lvs"] == []
    assert fp.deck_lines(tech, db, "drc", _roots(tmp_path))["drc"] == both
    db["vlsi.core.lvs_tool"] = "hammer.lvs.pegasus"
    del db["drc.magic.rcfile"]
    assert fp.deck_lines(tech, db, "lvs", _roots(tmp_path))["lvs"] == []


def test_magic_drc_reads_the_files_beside_its_rcfile(tmp_path):
    rcfile = _file(tmp_path / "pdk" / "magic" / "t.magicrc")
    techfile = _file(tmp_path / "pdk" / "magic" / "t.tech")
    tech = _tech(tmp_path)
    db = {"vlsi.core.drc_tool": "hammer.drc.magic", "drc.magic.rcfile": str(rcfile)}
    out = fp.merge_scopes(fp.config_lines(db, _roots(tmp_path), "drc"), fp.deck_lines(tech, db, "drc", _roots(tmp_path)))
    assert out["drc"] == sorted([_line(rcfile, tmp_path), _line(techfile, tmp_path)])
    techfile.write_text("changed tech\n")
    assert fp.deck_lines(tech, db, "drc", _roots(tmp_path))["drc"] != out["drc"]
    db["vlsi.core.drc_tool"] = "hammer.drc.pegasus"
    assert fp.deck_lines(tech, db, "drc", _roots(tmp_path))["drc"] == []


def test_gds_map_reaches_par_in_auto_mode_only(tmp_path):
    gds_map = _file(tmp_path / "pdk" / "layers.map")
    tech = _tech(tmp_path, gds_map_file=str(gds_map))
    db = {"par.inputs.gds_map_mode": "auto"}
    assert fp.deck_lines(tech, db, "par", _roots(tmp_path))["par"] == [_line(gds_map, tmp_path)]
    assert fp.deck_lines(tech, db, "drc", _roots(tmp_path))["drc"] == []
    for mode in ("manual", "empty"):
        assert fp.deck_lines(tech, {"par.inputs.gds_map_mode": mode}, "par", _roots(tmp_path))["par"] == []
    packaged = _tech(tmp_path, gds_map_file="defaults.yml")
    assert fp.deck_lines(packaged, db, "par", _roots(tmp_path))["par"] == [
        fp.file_line(os.path.join(fp.hammer_dir(), "technology", "nop", "defaults.yml"), _roots(tmp_path))]
    missing = _tech(tmp_path, gds_map_file="nosuch.map")
    assert fp.deck_lines(missing, db, "par", _roots(tmp_path))["par"] == ["UNRESOLVED:nosuch.map"]


def test_unresolvable_decks_give_a_stable_line_and_narrow_nothing(tmp_path):
    tech = _tech(tmp_path, drc_decks=[hammer_tech.DRCDeck(tool_name="pegasus", deck_name="d", path="nosuch.pvl"),
                                      hammer_tech.DRCDeck(tool_name="pegasus", deck_name="e", path="pdk/x.pvl")])
    out = fp.deck_lines(tech, {"vlsi.core.drc_tool": "hammer.drc.pegasus"}, "drc", _roots(tmp_path))
    assert out == {"drc": ["UNRESOLVED:nosuch.pvl", "UNRESOLVED:pdk/x.pvl"], fp.NARROWED_SCOPE: []}


def test_paths_inside_the_tech_additional_text(tmp_path):
    waiver = _file(tmp_path / "pdk" / "waivers.pvl")
    lvs_inc = _file(tmp_path / "pdk" / "lvs_extra.rul")
    tech = _tech(tmp_path, additional_drc_text=f'include "{waiver}"\nRULE x {{ /VSS }}\n',
                 additional_lvs_text=f"INCLUDE {lvs_inc}\n")
    db = {"drc.inputs.additional_drc_text_mode": "append", "lvs.inputs.additional_lvs_text_mode": "auto"}
    assert fp.deck_lines(tech, db, "drc", _roots(tmp_path))["drc"] == [_line(waiver, tmp_path)]
    assert fp.deck_lines(tech, db, "lvs", _roots(tmp_path))["lvs"] == [_line(lvs_inc, tmp_path)]
    db["drc.inputs.additional_drc_text_mode"] = "manual"
    assert fp.deck_lines(tech, db, "drc", _roots(tmp_path))["drc"] == []


def test_pegasus_rules_dir_walk_covers_the_deck_siblings(tmp_path):
    rules = tmp_path / "cds" / "Sky130_DRC"
    deck = _file(rules / "drc.pvl")
    beside = _file(rules / "layers.pvl")
    nested = _file(rules / "sub" / "density.pvl")
    tech = _tech(tmp_path, drc_decks=[hammer_tech.DRCDeck(tool_name="pegasus", deck_name="d", path=str(deck))])
    db = {"vlsi.core.drc_tool": "hammer.drc.pegasus", "vlsi.core.technology": "hammer.technology.t",
          "technology.t.pegasus_drc_rules_dir": str(rules)}
    assert fp.deck_lines(tech, db, "drc", _roots(tmp_path))["drc"] == [_line(deck, tmp_path)]
    assert _scopes(tmp_path, tech, db, "drc")["drc"] == sorted(
        _line(p, tmp_path) for p in (deck, beside, nested))
