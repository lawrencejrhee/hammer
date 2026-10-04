import json
import os

import pydantic
import pytest

import hammer.config as hammer_config
import hammer.tech as hammer_tech
from hammer.logging import HammerVLSILogging
from hammer.vlsi import HammerVLSISettings
from hammer.vlsi import fingerprints as fp


def _file(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _tech(tmp_path, monkeypatch, name, libraries, project):
    pkg = tmp_path / "pkgs" / name
    _file(pkg / "__init__.py", f"from hammer.tech import HammerTechnology\n"
                               f"class T(HammerTechnology):\n    pass\ntech = T()\n")
    _file(pkg / "plugin.gds")
    installs = [{"id": "pdkroot", "path": f"technology.{name}.pdk_root"},
                {"id": "unset", "path": f"technology.{name}.not_configured"}]
    (pkg / f"{name}.tech.json").write_text(json.dumps({"name": name, "installs": installs,
                                                       "libraries": libraries}))
    monkeypatch.syspath_prepend(str(tmp_path / "pkgs"))
    tech = hammer_tech.HammerTechnology.load_from_module(name)
    tech.logger = HammerVLSILogging.context("tech")
    tech.cache_dir = str(tmp_path / "obj" / f"tech-{name}-cache")
    database = hammer_config.HammerDatabase()
    database.update_technology(*tech.get_config())
    HammerVLSISettings.load_builtins_and_core(database)
    database.update_project([dict({"vlsi.core.technology": name,
                                   f"technology.{name}.pdk_root": str(tmp_path / "pdk")}, **project)])
    tech.set_database(database)
    return tech


def _roots(tmp_path, tech):
    return fp.make_roots(tech.cache_dir, str(tmp_path / "obj"), hammer_root="")


def test_every_path_field_is_resolved_and_scoped(tmp_path, monkeypatch):
    pdk, obj = tmp_path / "pdk", tmp_path / "obj"
    std = {
        "name": "std",
        "lef_file": "cache/std.lef",
        "verilog_sim": "cache/primitives.v",
        "spice_file": "cache/std.cdl",
        "nldm_liberty_file": "pdkroot/lib/std.lib",
        "ecsm_liberty_file": "pdkroot/lib/std_ecsm.lib",
        "qrc_techfile": "pdkroot/qrc/qrc.tch",
        "spice_model_file": {"path": "pdkroot/models/all.spice", "lib_corner": "pdkroot/models/tt.spice"},
        "itf_files": {"max_cap": "pdkroot/itf/max.itf", "min_cap": "pdkroot/itf/min.itf"},
        "tluplus_files": {"max_cap": "pdkroot/tlu/max.tluplus", "min_cap": "pdkroot/tlu/min.tluplus"},
        "tluplus_map_file": "pdkroot/tlu/layers.map",
        "power_grid_library": "pdkroot/pgv",
        "provides": [{"lib_type": "stdcell"}],
    }
    odd = {"gds_file": "plugin.gds", "lef_file": "nowhere/x.lef", "def_file": "missing.def",
           "verilog_synth": "unset/x.v", "milkyway_lib_in_dir": "pdkroot/mw/lib"}
    extra = [{"library": {"lef_file": str(obj / "macro" / "macro.lef"), "spice_file": "m/macro.cdl",
                          "gds_file": "m/macro.gds"},
              "prefix": {"id": "m", "path": str(tmp_path / "m1")}},
             {"library": {"gds_file": "m/macro.gds"}, "prefix": {"id": "m", "path": str(tmp_path / "m2")}}]
    tech = _tech(tmp_path, monkeypatch, "libfields", [std, odd],
                 {"vlsi.technology.extra_libraries": extra})
    cache = tmp_path / "obj" / "tech-libfields-cache"
    for rel in ("std.lef", "primitives.v", "std.cdl"):
        _file(cache / rel)
    pdk_files = [_file(pdk / rel) for rel in (
        "lib/std.lib", "lib/std_ecsm.lib", "qrc/qrc.tch", "itf/max.itf", "itf/min.itf",
        "tlu/max.tluplus", "tlu/min.tluplus", "tlu/layers.map", "pgv/a.cl", "pgv/sub/b.cl", "mw/lib/c.db")]
    models = _file(pdk / "models" / "all.spice")
    _file(pdk / "models" / "tt.spice")
    macro_lef = _file(obj / "macro" / "macro.lef", "MACRO m\n")
    m1_gds, m2_gds = _file(tmp_path / "m1" / "macro.gds"), _file(tmp_path / "m2" / "macro.gds")
    macro_cdl = _file(tmp_path / "m1" / "macro.cdl")
    roots = _roots(tmp_path, tech)

    out = fp.library_lines(tech, roots)

    def line(p):
        return fp.file_line(str(p), roots)
    assert out["vlsi"] == sorted(
        [line(cache / "std.lef"), line(cache / "primitives.v")] + [line(p) for p in pdk_files]
        + [line(tmp_path / "pkgs" / "libfields" / "plugin.gds"), line(macro_lef), line(m1_gds), line(m2_gds),
           "UNRESOLVED:nowhere/x.lef", "UNRESOLVED:missing.def", "UNRESOLVED:unset/x.v"])
    assert out["lvs"] == sorted([line(cache / "std.cdl"), line(models), line(macro_cdl)])
    assert line(cache / "std.lef").startswith("<TECH_CACHE>/std.lef:sha256=")
    assert line(macro_lef).startswith("<OBJ_DIR>/macro/macro.lef:sha256=")
    assert set(fp.library_lines(tech, roots, stage_tag="par")) == {"vlsi"}
    assert fp.library_lines(tech, roots, stage_tag="par")["vlsi"] == out["vlsi"]


def test_raw_paths_resolve_once_per_prefix_set(tmp_path, monkeypatch):
    shared = {"lef_file": "pdkroot/a.lef", "gds_file": "pdkroot/a.gds"}
    tech = _tech(tmp_path, monkeypatch, "libdedup", [dict(shared, name=str(i)) for i in range(50)],
                 {"vlsi.technology.extra_libraries": [
                     {"library": {"lef_file": "p/a.lef"}, "prefix": {"id": "p", "path": str(tmp_path / "x")}},
                     {"library": {"lef_file": "p/a.lef"}, "prefix": {"id": "p", "path": str(tmp_path / "y")}}]})
    calls = []
    original = tech.prepend_dir_path
    monkeypatch.setattr(tech, "prepend_dir_path", lambda raw, lib=None: calls.append(raw) or original(raw, lib))
    out = fp.library_lines(tech, _roots(tmp_path, tech))
    assert sorted(calls) == ["p/a.lef", "p/a.lef", "pdkroot/a.gds", "pdkroot/a.lef"]
    assert len(out["vlsi"]) == 4


def test_bare_min_max_names_missing_from_the_plugin_are_unresolved(tmp_path, monkeypatch):
    lef = _file(tmp_path / "pdk" / "tech.lef")
    models = _file(tmp_path / "pdk" / "models.spice")
    tech = _tech(tmp_path, monkeypatch, "libbare", [{
        "lef_file": str(lef),
        "spice_model_file": {"path": str(models)},
        "itf_files": {"max_cap": "max.itf", "min_cap": "min.itf"},
        "tluplus_files": {"max_cap": "max.tluplus", "min_cap": "min.tluplus"},
        "provides": [{"lib_type": "technology"}]}], {})
    roots = _roots(tmp_path, tech)
    unresolved = ["UNRESOLVED:max.itf", "UNRESOLVED:min.itf", "UNRESOLVED:max.tluplus", "UNRESOLVED:min.tluplus"]
    out = fp.library_lines(tech, roots)
    assert out["vlsi"] == sorted([fp.file_line(str(lef), roots)] + unresolved)
    assert out["lvs"] == [fp.file_line(str(models), roots)]
    itf = _file(tmp_path / "pkgs" / "libbare" / "max.itf")
    assert fp.library_lines(tech, roots)["vlsi"] == sorted(
        [fp.file_line(str(lef), roots), fp.file_line(str(itf), roots)] + unresolved[1:])


def test_same_bytes_rewrite_of_a_cache_library_is_stable(tmp_path, monkeypatch):
    tech = _tech(tmp_path, monkeypatch, "libchurn", [{"lef_file": "cache/tech.lef"}], {})
    lef = _file(tmp_path / "obj" / "tech-libchurn-cache" / "tech.lef", "LAYER m1\n")
    roots = _roots(tmp_path, tech)
    before = fp.library_lines(tech, roots)
    os.utime(lef, ns=(1, 1))
    lef.write_text("LAYER m1\n")
    assert fp.library_lines(tech, roots) == before
    lef.write_text("LAYER m2\n")
    assert fp.library_lines(tech, roots) != before


class _FakeTech:
    def __init__(self, libraries, error):
        self.libraries = libraries
        self.error = error

    def get_available_libraries(self):
        return self.libraries

    def prepend_dir_path(self, raw, lib=None):
        if raw.startswith("bad/"):
            raise self.error
        return raw


def _validation_error():
    try:
        hammer_tech.PathPrefix(id="x", path=None)
    except pydantic.ValidationError as e:
        return e
    raise AssertionError("PathPrefix accepted a None path")


@pytest.mark.parametrize("error", [AssertionError("a"), ValueError("v"), KeyError("k"), TypeError("t"),
                                   _validation_error()])
def test_unresolvable_paths_give_a_stable_line(tmp_path, error):
    good = _file(tmp_path / "good.lef")
    tech = _FakeTech([hammer_tech.Library(lef_file="bad/x.lef", gds_file=str(good))], error)
    assert fp.library_lines(tech, ())["vlsi"] == sorted(["UNRESOLVED:bad/x.lef", fp.file_line(str(good))])


def test_other_resolution_errors_propagate():
    tech = _FakeTech([hammer_tech.Library(lef_file="bad/x.lef")], RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        fp.library_lines(tech, ())


def test_names_corners_supplies_and_prefixes_are_not_paths(tmp_path):
    lib = hammer_tech.Library(
        name=str(tmp_path / "name.lef"),
        corner=hammer_tech.Corner(nmos="tt", pmos="tt", temperature="25 C"),
        supplies=hammer_tech.Supplies(GND="VSS", VDD="VDD"),
        extra_prefixes=[hammer_tech.PathPrefix(id="p", path=str(tmp_path))],
        spice_model_file=hammer_tech.SpiceModelFile(path=str(tmp_path / "m.spice"), lib_corner=str(tmp_path / "c")))
    assert list(fp._library_paths(lib, (hammer_tech.SpiceModelFile, hammer_tech.MinMaxCap))) == [
        ("spice_model_file", str(tmp_path / "m.spice"))]
