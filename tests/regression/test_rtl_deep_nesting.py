import hashlib
import sys

import pytest

from hammer.vlsi import rtl_check


@pytest.fixture
def slang() -> None:
    """Skip unless the pinned slang is reachable ($SLANG_BIN, PATH or tools/slang)."""
    try:
        rtl_check._checked_binary()
    except rtl_check.SlangNotFound as e:
        pytest.skip(f"needs slang {rtl_check.SLANG_VERSION}: {e}")


def test_deeply_nested_rtl_fingerprints(tmp_path, slang) -> None:
    terms = " ^ ".join(f"a[{i % 8}]" for i in range(1500))
    rtl = tmp_path / "top.sv"
    rtl.write_text(f"module top(input logic [7:0] a, output logic y);\n  assign y = {terms};\nendmodule\n")
    limit = sys.getrecursionlimit()
    overall, units = rtl_check.digest_files([str(rtl)], top_module="top")
    assert units and overall != hashlib.sha256(b"").hexdigest()
    assert sys.getrecursionlimit() == limit
