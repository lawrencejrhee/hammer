import hashlib
import json
import os

import pytest

from hammer.logging.test import HammerLoggingCaptureContext
from hammer.vlsi import CLIDriver, pd_store, rtl_check

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
        monkeypatch.setattr(rtl_check, "_run_slang", _slang_must_not_run)
        assert pd_store.compute_rtl_fingerprint([]) == EMPTY_FINGERPRINT

    def test_action_without_rtl_runs(self, tmpdir, monkeypatch) -> None:
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


@pytest.fixture
def slang() -> None:
    """Skip unless the pinned slang is reachable ($SLANG_BIN, PATH or tools/slang)."""
    try:
        rtl_check._checked_binary()
    except rtl_check.SlangNotFound as e:
        pytest.skip(f"needs slang {rtl_check.SLANG_VERSION}: {e}")


TOP_SV = """\
module leaf(input logic clk, input logic d, output logic q);
  always_ff @(posedge clk) q <= d;
endmodule

module top(input logic clk, input logic d, output logic q);
  logic mid;
  leaf u0(.clk(clk), .d(d), .q(mid));
  leaf u1(.clk(clk), .d(mid), .q(q));
endmodule
"""


class TestRealFingerprint:
    def test_hierarchy_and_edits(self, tmp_path, slang) -> None:
        rtl = tmp_path / "top.sv"
        rtl.write_text(TOP_SV)
        first, units = rtl_check.digest_files([str(rtl)], top_module="top")
        assert units and first != EMPTY_FINGERPRINT
        assert rtl_check.digest_files([str(rtl)], top_module="top")[0] == first
        rtl.write_text("// a comment\n" + TOP_SV.replace("q <= d;", "q <= d;  "))
        assert rtl_check.digest_files([str(rtl)], top_module="top")[0] == first
        rtl.write_text(TOP_SV.replace("q <= d;", "q <= ~d;"))
        assert rtl_check.digest_files([str(rtl)], top_module="top")[0] != first

    def test_bad_top_module(self, tmp_path, slang) -> None:
        rtl = tmp_path / "top.sv"
        rtl.write_text(TOP_SV)
        with pytest.raises(rtl_check.RtlParseError):
            rtl_check.digest_files([str(rtl)], top_module="no_such_module")

    def test_non_regular_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            rtl_check.digest_files([os.devnull])

    def test_missing_slang(self, tmp_path, monkeypatch) -> None:
        rtl = tmp_path / "top.sv"
        rtl.write_text(TOP_SV)
        monkeypatch.setenv("SLANG_BIN", str(tmp_path / "no_slang_here"))
        with pytest.raises(rtl_check.SlangNotFound):
            rtl_check.digest_files([str(rtl)], top_module="top")

    def test_driver_stores_fingerprint(self, tmpdir, slang) -> None:
        rtl = os.path.join(tmpdir, "top.sv")
        with open(rtl, "w") as f:
            f.write(TOP_SV)
        os.mkdir(os.path.join(tmpdir, "mock"))
        cfg = os.path.join(tmpdir, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({
                "vlsi.core.technology": "hammer.technology.nop",
                "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
                "synthesis.inputs.top_module": "top",
                "synthesis.inputs.input_files": [rtl],
                "synthesis.mocksynth.temp_folder": os.path.join(tmpdir, "mock"),
            }, f)
        obj = os.path.join(tmpdir, "obj")
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=[
                "syn",
                "-p", cfg,
                "--obj_dir", obj,
                "--syn_rundir", os.path.join(tmpdir, "syn"),
                "--log", os.path.join(tmpdir, "log.txt"),
            ])
        assert cm.value.code == 0
        with open(os.path.join(obj, "master_database.json")) as f:
            stored = json.load(f)["vlsi.rtl_fingerprint_sha256"]
        assert stored == rtl_check.digest_files([rtl], top_module="top")[0]
