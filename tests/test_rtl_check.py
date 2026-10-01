#  Tests for hammer.vlsi.rtl_check, the slang-based RTL fingerprint

import hashlib
import json
import os

import pytest

from hammer.logging.test import HammerLoggingCaptureContext
from hammer.vlsi import CLIDriver, pd_store, rtl_check

# The fingerprint of no RTL: the hash of an empty manifest, as before slang. It has to
# stay a fixed value that needs no slang, because synthesis.inputs.input_files
# defaults to [] and every action that lists no RTL computes it.
EMPTY_FINGERPRINT = hashlib.sha256(b"").hexdigest()


def _slang_must_not_run(*args, **kwargs):
    raise AssertionError("slang ran for an empty input list")


class TestEmptyInputs:
    def test_digest_of_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(rtl_check, "_run_slang", _slang_must_not_run)
        assert rtl_check.digest_files([]) == (EMPTY_FINGERPRINT, [])
        assert rtl_check.digest_units([], include_dirs=["inc"], defines=["SYNTHESIS"],
                                      top_module="top") == (EMPTY_FINGERPRINT, [])

    def test_no_slang_needed(self, monkeypatch) -> None:
        monkeypatch.setenv("SLANG_BIN", "/nonexistent/slang")
        assert rtl_check.digest_files([])[0] == EMPTY_FINGERPRINT

    def test_cache_key_agrees(self, monkeypatch) -> None:
        # pd_cache falls back to pd_store.compute_rtl_fingerprint, which must give the
        # same value cli_driver stores.
        monkeypatch.setattr(rtl_check, "_run_slang", _slang_must_not_run)
        assert pd_store.compute_rtl_fingerprint([]) == EMPTY_FINGERPRINT

    def test_action_without_rtl_runs(self, tmpdir, monkeypatch) -> None:
        # Chipyard runs sram_generator with no synthesis inputs; it used to abort in
        # the fingerprint ("error: no input files") before the tool ran.
        monkeypatch.setattr(rtl_check, "_run_slang", _slang_must_not_run)
        cfg = os.path.join(tmpdir, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({
                "vlsi.core.technology": "hammer.technology.nop",
                "vlsi.core.sram_generator_tool": "hammer.sram_generator.nop",
                "vlsi.inputs.sram_parameters": [],
            }, f)
        with HammerLoggingCaptureContext() as c:
            with pytest.raises(SystemExit) as cm:
                CLIDriver().main(args=[
                    "sram_generator",
                    "-p", cfg,
                    "--obj_dir", os.path.join(tmpdir, "obj"),
                    "--log", os.path.join(tmpdir, "log.txt"),
                ])
            assert cm.value.code == 0
        assert not c.log_contains("fingerprint")
