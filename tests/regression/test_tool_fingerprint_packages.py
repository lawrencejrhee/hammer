import ast
import sys
import uuid

import pytest

from hammer.vlsi import code_fingerprints as cf


TOOL_SRC = '''from hammer.synthesis.{b} import MixinB
from {ns}.mixin import MixinN


def _converter():
    try:
        from hammer.lvs.{c} import convert
    except ImportError:
        convert = None
    return convert


class ToolA(MixinB, MixinN):
    pass


tool = ToolA
'''


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def pkgs(tmp_path, monkeypatch):
    tag = uuid.uuid4().hex[:8]
    names = {k: f"fptool{k}_{tag}" for k in ("a", "b", "c", "d", "ns")}
    root = tmp_path / "site"
    a = root / "hammer" / "par" / names["a"]
    _write(a / "__init__.py", TOOL_SRC.format(**names))
    _write(a / "defaults.yml", "par.toola:\n  knob: 1 # tuned\n")
    _write(a / "helpers" / "fix.tcl", "puts fix\nset x 1\n")
    _write(root / "hammer" / "synthesis" / names["b"] / "__init__.py",
           "class MixinB:\n    def go(self):\n        return 1\n")
    _write(root / "hammer" / "lvs" / names["c"] / "__init__.py", "def convert(x):\n    return x\n")
    _write(root / "hammer" / "drc" / names["d"] / "__init__.py", "def unrelated():\n    return 0\n")
    _write(root / names["ns"] / "mixin.py", "class MixinN:\n    pass\n")
    _write(root / names["ns"] / "shared.py", "LIMIT = 1\n")
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setattr(cf, "_FIRST", {})
    yield root, names, f"hammer.par.{names['a']}"
    for mod in [m for m in sys.modules if tag in m]:
        del sys.modules[mod]


def _key(module, monkeypatch):
    monkeypatch.setattr(cf, "_FIRST", {})
    return cf.tool_fingerprint(module)


def test_tool_key_follows_defaults_and_tcl_helpers(pkgs, monkeypatch):
    root, names, module = pkgs
    a = root / "hammer" / "par" / names["a"]
    base = _key(module, monkeypatch)

    (a / "defaults.yml").write_text("# header\npar.toola: {knob: 1}\n")
    assert _key(module, monkeypatch) == base
    (a / "defaults.yml").write_text("par.toola:\n  knob: 2\n")
    assert _key(module, monkeypatch) != base
    (a / "defaults.yml").write_text("par.toola:\n  knob: 1\n")
    assert _key(module, monkeypatch) == base

    (a / "helpers" / "fix.tcl").write_bytes(b"puts fix\r\nset x 1\r\n")
    assert _key(module, monkeypatch) == base
    (a / "helpers" / "fix.tcl").write_text("puts fix\nset x 2\n")
    assert _key(module, monkeypatch) != base


def test_tool_key_follows_mixins_and_imported_packages(pkgs, monkeypatch):
    root, names, module = pkgs
    base = _key(module, monkeypatch)
    lines = cf.tool_lines(module)
    for label in (module, f"hammer.synthesis.{names['b']}", f"hammer.lvs.{names['c']}", names["ns"]):
        assert any(line.startswith(label + "/") for line in lines), label
    assert not any(names["d"] in line for line in lines)

    (root / "hammer" / "drc" / names["d"] / "__init__.py").write_text("def unrelated():\n    return 9\n")
    assert _key(module, monkeypatch) == base

    for path, text in ((root / "hammer" / "synthesis" / names["b"] / "__init__.py",
                        "class MixinB:\n    def go(self):\n        return 2\n"),
                       (root / "hammer" / "lvs" / names["c"] / "__init__.py", "def convert(x):\n    return -x\n"),
                       (root / names["ns"] / "shared.py", "LIMIT = 2\n")):
        old = path.read_text()
        path.write_text(text)
        assert _key(module, monkeypatch) != base, path
        path.write_text(old)
        assert _key(module, monkeypatch) == base, path


def test_editor_artefacts_and_bytecode_do_not_change_the_tool_key(pkgs, monkeypatch):
    root, names, module = pkgs
    a = root / "hammer" / "par" / names["a"]
    base = _key(module, monkeypatch)
    for name in (".fix.tcl.swp", "fix.tcl~", "#fix.tcl#", "fix.tcl.orig", "x.rej", "README.md", "out.tmp.12.3"):
        (a / "helpers" / name).write_text("junk\n")
    _write(a / "__pycache__" / "__init__.cpython-311.pyc", "bytecode")
    assert _key(module, monkeypatch) == base
    assert not any("__pycache__" in line or ".pyc" in line for line in cf.tool_lines(module))


def test_no_tool_configured_gives_a_constant_key():
    assert cf.tool_fingerprint(None) == cf.tool_fingerprint("") == cf.tool_fingerprint(None)


def test_real_tools_cover_shared_and_imported_packages():
    innovus = cf.tool_lines("hammer.par.innovus")
    assert any(line.startswith("hammer.par.innovus/defaults.yml=data:") for line in innovus)
    assert any(line.startswith("hammer.common.cadence/__init__.py=") for line in innovus)
    assert not any(line.startswith(("hammer.vlsi.", "hammer.config", "hammer.tech")) for line in innovus)
    assert any(line.startswith("hammer.par.innovus/") for line in cf.tool_lines("hammer.synthesis.innovus_plus"))
    assert any(line.startswith("hammer.vlsi.vendor/openroad.py=") for line in cf.tool_lines("hammer.par.openroad"))


def test_tool_import_scan_parses_each_file_once_per_content(pkgs, monkeypatch):
    root, names, module = pkgs
    first = cf.tool_lines(module)
    calls = []
    parse = ast.parse

    def counting(*args, **kwargs):
        calls.append(args)
        return parse(*args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting)
    assert cf.tool_lines(module) == first
    assert calls == []
    (root / names["ns"] / "mixin.py").write_text("class MixinN:\n    LIMIT = 3\n")
    cf.tool_lines(module)
    assert len(calls) == 1
