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


def _fake_slang(monkeypatch, codes):
    """Make subprocess.run return each exit code in turn, recording the commands."""
    import subprocess
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        code = codes[min(len(calls), len(codes)) - 1]
        if code == 0 and "--ast-json" in cmd:
            out = cmd[cmd.index("--ast-json") + 1]
            with open(out, "w") as f:
                f.write('{"design": {"members": [{"kind": "Instance", "name": "top"}]}, "definitions": []}')
        return subprocess.CompletedProcess(cmd, code, "", "")

    monkeypatch.setattr(rtl_check, "_checked_binary", lambda: "slang")
    monkeypatch.setattr(rtl_check.subprocess, "run", run)
    return calls


def test_a_slang_crash_retries_single_threaded(tmp_path, monkeypatch) -> None:
    rtl = tmp_path / "top.sv"
    rtl.write_text("module top; endmodule\n")
    calls = _fake_slang(monkeypatch, [-10, 0])
    overall, _ = rtl_check.digest_files([str(rtl)], top_module="top")
    assert len(calls) == 3 and "-E" in calls[2]
    assert "--threads" not in calls[0]
    assert calls[1][1:3] == ["--threads", "1"]
    assert not overall.startswith("bytes:")


def test_a_repeated_slang_crash_names_the_signal(tmp_path, monkeypatch) -> None:
    rtl = tmp_path / "top.sv"
    rtl.write_text("module top; endmodule\n")
    _fake_slang(monkeypatch, [-10, -10])
    with pytest.raises(rtl_check.RtlParseError, match="signal 10"):
        rtl_check.digest_files([str(rtl)], top_module="top")
    digest, reason = rtl_check.digest_or_bytes([str(rtl)], top_module="top")
    assert digest.startswith("bytes:") and "signal 10" in reason
