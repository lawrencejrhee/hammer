import json
from types import SimpleNamespace

import pytest

from hammer.config import HammerDatabase
from hammer.vlsi import fingerprints as fp
from hammer.vlsi import pd_cache, pd_store


def _file(path, data="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    return path


def _driver(obj, db):
    return SimpleNamespace(database=SimpleNamespace(get_database_json=lambda: json.dumps(db)), obj_dir=str(obj))


def _keys(tmp_path, make_db, stage="synthesis"):
    keys = []
    for name in ("objA", "objB"):
        obj = tmp_path / name
        keys.append(pd_cache._build_cache_key(_driver(obj, make_db(obj)), stage))
    return keys


def _base(obj, **extra):
    return dict({"synthesis.inputs.top_module": "top", "vlsi.rtl_fingerprint_sha256": "fixed"}, **extra)


def test_build_dir_files_share_a_key_across_build_dirs(tmp_path):
    for name in ("objA", "objB"):
        _file(tmp_path / name / "constraints" / "io.sdc", "set_load 1\n")
        _file(tmp_path / name / "tcl" / "fix.tcl", "puts fix\n")

    def db(obj):
        return _base(obj, **{"vlsi.inputs.custom_sdc_files": [str(obj / "constraints" / "io.sdc")],
                             "vlsi.inputs.custom_sdc_constraints": [f"source {obj / 'tcl' / 'fix.tcl'}"]})
    a, b = _keys(tmp_path, db)
    assert a == b


def test_build_dir_directories_and_missing_paths_keep_their_location(tmp_path):
    for name in ("objA", "objB"):
        _file(tmp_path / name / "ilm" / "Child" / "Child.timing", "same\n")
    a, b = _keys(tmp_path, lambda obj: _base(obj, **{"vlsi.inputs.some_dir": str(obj / "ilm")}))
    assert a != b
    a, b = _keys(tmp_path, lambda obj: _base(obj, **{"vlsi.inputs.custom_sdc_files": [str(obj / "gone.sdc")]}))
    assert a != b


def test_a_file_outside_the_roots_keeps_its_path(tmp_path):
    ext = _file(tmp_path / "pdk" / "io.sdc")
    out = pd_cache._normalize_roots({"k": str(ext)}, fp.make_roots(None, str(tmp_path / "obj"), ""), {}, {})
    assert out == {"k": str(ext)}


def test_the_action_memo_verdict_holds_for_the_whole_action(tmp_path):
    obj = tmp_path / "obj"
    db = _base(obj, **{"vlsi.inputs.custom_sdc_files": [str(obj / "late.sdc")]})
    driver = _driver(obj, db)
    memo = fp.begin_action_memo(driver)
    fp.config_lines(db, fp.path_roots(driver), "synthesis", memo=memo)
    before = pd_cache._build_cache_key(driver, "synthesis")
    _file(obj / "late.sdc")
    assert pd_cache._build_cache_key(driver, "synthesis") == before
    fp.begin_action_memo(driver)
    assert pd_cache._build_cache_key(driver, "synthesis") != before


PROBES = ("par.knob", "par.outputs.gds", "parx.knob", "par_extra.knob", "synthesis.knob", "sram_generator.knob",
          "simulation.knob", "vlsi.knob", "technology.t.knob", "lvs.knob", "lvsx.knob", "custom.knob")


@pytest.mark.parametrize("stage,tag", [("syn", "synthesis"), ("par", "par"), ("drc", "drc"), ("lvs", "lvs")])
def test_cache_slice_is_what_the_dependency_check_compares(tmp_path, stage, tag):
    for i, key in enumerate(PROBES):
        db = HammerDatabase()
        db.update_project([{key: 1}])
        master = str(tmp_path / f"{stage}-{i}.json")
        db.stage_change_check(stage, master)
        db.commit_master_database()
        db.update_project([{key: 2}])
        in_slice = key in pd_store._stage_relevant_keys({key: 2}, tag)
        assert db.stage_change_check(stage, master) == in_slice, key


def test_sram_generator_keys_are_global_in_every_slice():
    for tag in fp.OWNED_STAGE_TAGS:
        assert "sram_generator.knob" in pd_store._stage_relevant_keys({"sram_generator.knob": 1}, tag)


def test_a_tag_no_stage_owns_has_no_upstream_lines(tmp_path):
    obj = tmp_path / "obj"
    db = {"sram_generator.inputs.config": str(_file(obj / "sram" / "cfg.json"))}
    assert fp.upstream_lines(db, fp.make_roots(None, str(obj), ""), "sram_generator") == []
    assert pd_cache._build_cache_key(_driver(obj, db), "sram_generator")
