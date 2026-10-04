import os
import re

import pytest

from hammer.vlsi import code_fingerprints as cf


GUARDED_AREAS = ("vlsi", "tech", "utils")


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(cf, "_FIRST", {})
    monkeypatch.setattr(cf, "_SNAPSHOT", {})
    monkeypatch.setattr(cf, "_SNAPSHOT_DIGEST", {})


def _modules(root):
    for area in GUARDED_AREAS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, area)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if name.endswith(".py"):
                    yield os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")


def test_every_framework_area_module_is_classified():
    allow, infra = set(cf._FRAMEWORK_FILES), set(cf._INFRA_FILES)
    assert not allow & infra
    unclassified = [rel for rel in _modules(cf._HAMMER)
                    if rel not in allow and rel not in infra
                    and not any(rel.startswith(p + "/") for p in cf._TOOL_SIDE_PACKAGES)]
    assert unclassified == [], "add each new module to _FRAMEWORK_FILES or _INFRA_FILES in code_fingerprints.py"
    listed = cf._FRAMEWORK_FILES + cf._INFRA_FILES
    assert [rel for rel in listed if not os.path.isfile(os.path.join(cf._HAMMER, rel))] == []


def test_framework_key_is_stable_and_has_one_line_per_allowlisted_file():
    lines = cf.framework_lines()
    assert [line.split("=", 1)[0] for line in lines] == list(cf._FRAMEWORK_FILES)
    assert not any(line.endswith(("=MISSING", "=UNREADABLE")) or line.split("=", 1)[1].startswith("inmem:")
                   for line in lines)
    assert not any(".pyc" in line for line in lines)
    key = cf.framework_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert cf.framework_fingerprint() == key


def test_framework_code_edit_changes_the_key_but_comments_do_not(tmp_path, monkeypatch, fresh):
    monkeypatch.setattr(cf, "_FRAMEWORK_FILES", ("vlsi/constraints.py", "utils/table.txt"))
    (tmp_path / "vlsi").mkdir()
    (tmp_path / "utils").mkdir()
    mod = tmp_path / "vlsi" / "constraints.py"
    data = tmp_path / "utils" / "table.txt"
    mod.write_text("def pitch():\n    return 2\n")
    data.write_text("a 1\n")
    base = cf.framework_fingerprint(str(tmp_path))

    mod.write_text('def pitch():\n    """Track pitch."""\n    # in microns\n    return 2\n')
    monkeypatch.setattr(cf, "_FIRST", {})
    assert cf.framework_fingerprint(str(tmp_path)) == base

    mod.write_text("def pitch():\n    return 3\n")
    monkeypatch.setattr(cf, "_FIRST", {})
    assert cf.framework_fingerprint(str(tmp_path)) != base

    mod.write_text("def pitch():\n    return 2\n")
    monkeypatch.setattr(cf, "_FIRST", {})
    assert cf.framework_fingerprint(str(tmp_path)) == base
    data.write_text("a 2\n")
    assert cf.framework_fingerprint(str(tmp_path)) != base


def test_missing_framework_file_gives_a_stable_line(tmp_path, monkeypatch, fresh):
    monkeypatch.setattr(cf, "_FRAMEWORK_FILES", ("vlsi/gone.py",))
    assert cf.framework_lines(str(tmp_path)) == ["vlsi/gone.py=MISSING"]


@pytest.mark.parametrize("snapshot_via", ["link", "real"])
def test_symlinked_checkout_matches_its_framework_snapshot(tmp_path, monkeypatch, fresh, snapshot_via):
    monkeypatch.setattr(cf, "_WARNED", set())
    monkeypatch.setattr(cf, "_FRAMEWORK_FILES", ("vlsi/constraints.py",))
    real = tmp_path / "real"
    (real / "vlsi").mkdir(parents=True)
    mod = real / "vlsi" / "constraints.py"
    mod.write_text("def pitch():\n    return 2\n")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    snap_root, lookup_root = (link, real) if snapshot_via == "link" else (real, link)
    cf._take_snapshot(str(snap_root), cf._FRAMEWORK_FILES)
    [loaded] = cf.framework_lines(str(lookup_root))
    mod.write_text("def pitch():\n    return 3\n")
    assert cf.framework_lines(str(lookup_root)) == [loaded.replace("=", "=inmem:", 1)]
    assert cf.code_digest(str(lookup_root / "vlsi" / "constraints.py")).startswith("inmem:")
