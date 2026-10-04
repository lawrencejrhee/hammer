import importlib
import sys
import uuid

import pytest

from hammer.tech import DRCDeck, Library, TechConfig
from hammer.vlsi import code_fingerprints as cf
from hammer.vlsi import fingerprints as fp


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def techpkg(tmp_path, monkeypatch):
    name = f"fptech_{uuid.uuid4().hex[:8]}"
    pkg = tmp_path / "site" / name
    _write(pkg / "__init__.py", "class FakeTech:\n    pass\n")
    _write(pkg / "defaults.yml", "technology.fake.x: 1\n")
    _write(pkg / "defaults_types.yml", "technology.fake.x: int\n")
    _write(pkg / "sram-cache.json", '{"srams": [{"name": "s1", "width": 32}]}\n')
    _write(pkg / "extra" / "setRC.tcl", "set rc 1\n")
    _write(pkg / "extra" / "gen-io-file.py", "print('dev script')\n")
    _write(pkg / "helper.py", "def scale():\n    return 1\n")
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    monkeypatch.setattr(cf, "_FIRST", {})
    mod = importlib.import_module(name)
    yield pkg, mod
    sys.modules.pop(name, None)


def _tech(mod, obj, **changes):
    tech = mod.FakeTech()
    tech.package = mod.__name__
    fields = dict(name="fake", gds_map_file=str(obj / "tech-cache" / "map.txt"),
                  libraries=[Library(lef_file=str(obj / "tech-cache" / "cells.lef")),
                             Library(nldm_liberty_file="/pdk/lib/tt.lib")],
                  drc_decks=[DRCDeck(tool_name="calibre", deck_name="all", path=str(obj / "deck.rul"))])
    fields.update(changes)
    tech.config = TechConfig(**fields)
    return tech


def _key(tech, obj, monkeypatch):
    monkeypatch.setattr(cf, "_FIRST", {})
    return cf.tech_fingerprint(tech, fp.make_roots(str(obj / "tech-cache"), str(obj), hammer_root=""))


def test_tech_key_is_the_same_in_another_build_dir(techpkg, tmp_path, monkeypatch):
    _, mod = techpkg
    a, b = tmp_path / "objA", tmp_path / "elsewhere" / "objB"
    assert _key(_tech(mod, a), a, monkeypatch) == _key(_tech(mod, b), b, monkeypatch)


def test_tech_config_edit_changes_the_key_but_decks_do_not(techpkg, tmp_path, monkeypatch):
    _, mod = techpkg
    obj = tmp_path / "obj"
    base = _key(_tech(mod, obj), obj, monkeypatch)
    assert _key(_tech(mod, obj, gds_map_file="/pdk/other.map"), obj, monkeypatch) != base
    assert _key(_tech(mod, obj, libraries=[Library(nldm_liberty_file="/pdk/lib/tt.lib")]), obj, monkeypatch) != base
    other_deck = [DRCDeck(tool_name="calibre", deck_name="all", path="/pdk/moved.rul")]
    assert _key(_tech(mod, obj, drc_decks=other_deck), obj, monkeypatch) == base


def test_package_data_counts_but_defaults_main_module_and_dev_scripts_do_not(techpkg, tmp_path, monkeypatch):
    pkg, mod = techpkg
    obj = tmp_path / "obj"
    tech = _tech(mod, obj)
    base = _key(tech, obj, monkeypatch)
    for rel, text in (("defaults.yml", "technology.fake.x: 2\n"),
                      ("defaults_types.yml", "technology.fake.x: str\n"),
                      ("__init__.py", "class FakeTech:\n    level = 2\n"),
                      ("extra/gen-io-file.py", "print('changed')\n"),
                      ("sram-cache.json", '{"srams":[{"width":32,"name":"s1"}]}'),
                      ("extra/setRC.tcl", "set rc 1\r\n"),
                      ("helper.py", "def scale():\n    # unit factor\n    return 1\n")):
        _write(pkg / rel, text)
        assert _key(tech, obj, monkeypatch) == base, rel
    for rel, text in (("sram-cache.json", '{"srams": [{"name": "s1", "width": 64}]}\n'),
                      ("extra/setRC.tcl", "set rc 2\n"),
                      ("helper.py", "def scale():\n    return 2\n"),
                      ("extra/new.map", "metal1 1 0\n")):
        _write(pkg / rel, text)
        assert _key(tech, obj, monkeypatch) != base, rel
        base = _key(tech, obj, monkeypatch)


def test_no_tech_gives_a_constant_key():
    assert cf.tech_fingerprint(None) == cf.tech_fingerprint(None, ())
