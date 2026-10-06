"""A slang that exists but cannot run must fall back to the byte hash, not stop the action."""
import os

import pytest

from hammer.vlsi import rtl_check


@pytest.fixture
def rtl(tmp_path):
    path = tmp_path / "top.sv"
    path.write_text("module top; endmodule\n")
    return str(path)


def test_a_non_executable_slang_bin_falls_back(tmp_path, monkeypatch, rtl) -> None:
    fake = tmp_path / "slang"
    fake.write_text("#!/bin/sh\necho 'slang version 11.0.0'\n")
    os.chmod(fake, 0o644)
    monkeypatch.setenv("SLANG_BIN", str(fake))
    with pytest.raises(rtl_check.SlangNotFound, match="not an executable"):
        rtl_check.slang_binary()
    digest, reason = rtl_check.digest_or_bytes([rtl], top_module="top")
    assert digest.startswith("bytes:") and "unavailable" in reason


def test_a_slang_for_another_platform_falls_back(tmp_path, monkeypatch, rtl) -> None:
    fake = tmp_path / "slang"
    fake.write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 64)  # not runnable as a script or here
    os.chmod(fake, 0o755)
    monkeypatch.setenv("SLANG_BIN", str(fake))

    def cannot_exec(cmd, **kwargs):
        raise OSError(8, "Exec format error", cmd[0])

    monkeypatch.setattr(rtl_check.subprocess, "run", cannot_exec)
    digest, reason = rtl_check.digest_or_bytes([rtl], top_module="top")
    assert digest.startswith("bytes:") and "cannot run" in reason
