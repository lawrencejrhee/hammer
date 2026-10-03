import json
import os

import pytest

from hammer.vlsi import CLIDriver, pd_store, rtl_check

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


@pytest.fixture
def slang() -> None:
    """Skip unless the pinned slang is reachable ($SLANG_BIN, PATH or tools/slang)."""
    try:
        rtl_check._checked_binary()
    except rtl_check.SlangNotFound as e:
        pytest.skip(f"needs slang {rtl_check.SLANG_VERSION}: {e}")


def test_unparsable_rtl_falls_back_to_bytes(tmp_path, slang) -> None:
    rtl = tmp_path / "top.sv"
    rtl.write_text(TOP_SV)
    overall, reason = rtl_check.digest_or_bytes([str(rtl)], top_module="no_such_module")
    assert overall.startswith("bytes:")
    assert "could not parse" in reason


class TestByteFallback:
    @pytest.fixture(autouse=True)
    def _no_slang(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SLANG_BIN", str(tmp_path / "no_slang_here"))

    def test_missing_slang_falls_back_to_bytes(self, tmp_path) -> None:
        rtl = tmp_path / "top.sv"
        rtl.write_text(TOP_SV)
        first, reason = rtl_check.digest_or_bytes([str(rtl)], top_module="top")
        assert first.startswith("bytes:")
        assert "unavailable" in reason
        assert rtl_check.digest_or_bytes([str(rtl)], top_module="top")[0] == first
        rtl.write_text(TOP_SV.replace("q <= d;", "q <= ~d;"))
        assert rtl_check.digest_or_bytes([str(rtl)], top_module="top")[0] != first
        assert rtl_check.digest_or_bytes([str(rtl)], top_module="leaf")[0] != rtl_check.digest_or_bytes(
            [str(rtl)], top_module="top")[0]

    def test_missing_rtl_file_still_fails(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            rtl_check.digest_or_bytes([str(tmp_path / "missing.sv")])

    def test_cache_key_fingerprint_agrees(self, tmp_path) -> None:
        rtl = tmp_path / "top.sv"
        rtl.write_text(TOP_SV)
        assert pd_store.compute_rtl_fingerprint([str(rtl)], top_module="top") == \
            rtl_check.digest_or_bytes([str(rtl)], top_module="top")[0]

    def test_driver_runs_without_slang(self, tmpdir, capsys) -> None:
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
                "-o", os.path.join(tmpdir, "output.json"),
                "--log", os.path.join(tmpdir, "log.txt"),
            ])
        assert cm.value.code == 0
        assert "fell back to a byte hash" in capsys.readouterr().out
        with open(os.path.join(obj, "master_database.json")) as f:
            assert json.load(f)["vlsi.rtl_fingerprint_sha256"].startswith("bytes:")
