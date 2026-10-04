import ast
import hashlib
import shutil
import sys

import pytest

from hammer.vlsi import code_fingerprints as cf


SRC = b'''"""Module doc."""
import os

X = 1  # a comment


class A:
    """Class doc."""

    def f(self, a, b=2):
        """Func doc."""
        return a + b
'''

SRC_REFORMATTED = b'''import os
X=1
class A:
    def f(self,a,b = 2):
        # another comment
        return (a + b)
'''

GOLDEN_SRC = b'''"""Doc."""
from __future__ import annotations
import os as _os
from .x import y, z as w


@decorator(1, key="v")
class K(Base, metaclass=Meta):
    """Doc."""
    attr: int = 3

    async def run(self, a, /, b: str = "s", *args, c, d=None, **kw) -> list:
        async with ctx() as c2:
            async for i in aiter():
                await i
        return [x for x in range(3) if x] + list({k: v for k, v in kw.items()})


def g(p):
    global G
    try:
        q = (n := p[1:2, ::3])
    except (KeyError, ValueError) as e:
        raise RuntimeError(f"{p!r:>10} {e}") from e
    finally:
        del q
    return lambda *a, **k: (*a, ...), b"\\x00", 1.5j, -1, not p, p if p else None
'''

GOLDEN = "7add6e2c33a6ed31bcc5f7bf3e05f5834517817fdb8c1c1a57b3a1596366edae"


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(cf, "_FIRST", {})
    monkeypatch.setattr(cf, "_SNAPSHOT", {})
    monkeypatch.setattr(cf, "_SNAPSHOT_DIGEST", {})
    monkeypatch.setattr(cf, "_WARNED", set())


def test_comments_docstrings_and_formatting_do_not_change_the_digest():
    assert cf.digest_source(SRC) == cf.digest_source(SRC_REFORMATTED)
    assert cf.digest_source(SRC) == cf.digest_source(SRC.replace(b"\n", b"\r\n"))
    assert not cf.digest_source(SRC).startswith("raw:")


def test_real_code_edits_change_the_digest():
    base = cf.digest_source(SRC)
    assert cf.digest_source(SRC.replace(b"a + b", b"a - b")) != base
    assert cf.digest_source(SRC.replace(b"b=2", b"b=3")) != base
    assert cf.digest_source(SRC.replace(b"X = 1", b"X = '1'")) != base


def test_neutral_dump_digest_is_pinned_across_python_versions():
    assert cf.digest_source(GOLDEN_SRC) == GOLDEN


def test_renamed_copy_and_extensionless_driver_share_the_ast_digest(tmp_path, fresh):
    src = tmp_path / "a.py"
    src.write_bytes(SRC)
    shutil.copy(src, tmp_path / "b.py")
    shutil.copy(src, tmp_path / "example-driver")
    digests = {cf.code_digest(str(tmp_path / n)) for n in ("a.py", "b.py", "example-driver")}
    assert len(digests) == 1
    assert not digests.pop().startswith("raw:")


@pytest.mark.parametrize("data", [b"def f(:\n    pass\n", b"x = 1\x00\n", b"x = '\xff\xfe'\n# -*- coding: ascii -*-\n"])
def test_unparsable_source_falls_back_to_raw_bytes(data):
    assert cf.digest_source(data) == "raw:" + hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("error", [RecursionError, MemoryError])
def test_parser_resource_errors_fall_back_to_raw_bytes(monkeypatch, error):
    def boom(*args, **kwargs):
        raise error()

    monkeypatch.setattr(cf, "_DIGEST_MEMO", {})
    monkeypatch.setattr(ast, "parse", boom)
    assert cf.digest_source(SRC) == "raw:" + hashlib.sha256(SRC).hexdigest()


def test_first_read_is_reused_for_the_rest_of_the_process(tmp_path, fresh, monkeypatch):
    mod = tmp_path / "helper.py"
    mod.write_bytes(SRC)
    first = cf.code_digest(str(mod))
    mod.write_bytes(SRC.replace(b"a + b", b"a * b"))
    assert cf.code_digest(str(mod)) == first
    monkeypatch.setattr(cf, "_FIRST", {})
    assert cf.code_digest(str(mod)) != first


def test_framework_file_edited_after_import_is_marked_inmem(tmp_path, fresh, capsys):
    (tmp_path / "vlsi").mkdir()
    mod = tmp_path / "vlsi" / "constraints.py"
    mod.write_bytes(SRC)
    cf._take_snapshot(str(tmp_path), ["vlsi/constraints.py"])
    loaded = cf.code_digest(str(mod))
    assert loaded == cf.digest_source(SRC)
    mod.write_bytes(SRC.replace(b"# a comment", b"# reworded"))
    assert cf.code_digest(str(mod)) == loaded
    mod.write_bytes(SRC.replace(b"a + b", b"a * b"))
    assert cf.code_digest(str(mod)) == "inmem:" + loaded
    assert "restart long-lived workers" in capsys.readouterr().err


def _depth():
    frame, n = sys._getframe(), 0
    while frame is not None:
        n += 1
        frame = frame.f_back
    return n


def _at_depth(level, fn):
    return fn() if _depth() >= level else _at_depth(level, fn)


@pytest.mark.parametrize("nesting", [600, 3000])
def test_deeply_nested_source_digest_does_not_depend_on_caller_depth(nesting):
    src = b"x = " + b"-" * nesting + b"1\n"
    shallow = cf._ast_digest(src)
    deep = _at_depth(sys.getrecursionlimit() - 40, lambda: cf._ast_digest(src))
    assert deep == shallow
    if nesting == 600:
        assert not shallow.startswith("raw:")
