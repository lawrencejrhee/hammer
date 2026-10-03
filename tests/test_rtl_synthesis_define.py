import json
from pathlib import Path

import pytest

from hammer.config import HammerJSONEncoder
from hammer.vlsi import CLIDriver, rtl_check

GUARDED = """module top(input clk, input [7:0] a, output [7:0] y);
  reg [7:0] r1;
  always @(posedge clk) r1 <= a;
`ifdef SYNTHESIS
  assign y = {SYN};
`else
  assign y = {SIM};
`endif
endmodule
"""


@pytest.fixture
def slang() -> None:
    try:
        rtl_check._checked_binary()
    except rtl_check.SlangNotFound as e:
        pytest.skip(f"needs slang {rtl_check.SLANG_VERSION}: {e}")


def _digest(tmp_path: Path, syn: str, sim: str, defines=()) -> str:
    rtl = tmp_path / "top.v"
    rtl.write_text(GUARDED.replace("{SYN}", syn).replace("{SIM}", sim))
    return rtl_check.digest_units([str(rtl)], defines=list(defines), top_module="top")[0]


class TestSynthesisDefine:
    def test_edit_inside_ifdef_synthesis_changes_the_fingerprint(self, tmp_path, slang) -> None:
        assert _digest(tmp_path, "r1", "a") != _digest(tmp_path, "~r1", "a")

    def test_edit_inside_ifndef_synthesis_keeps_the_fingerprint(self, tmp_path, slang) -> None:
        assert _digest(tmp_path, "r1", "a") == _digest(tmp_path, "r1", "~a")

    def test_explicit_synthesis_define_gives_the_same_fingerprint(self, tmp_path, slang) -> None:
        assert _digest(tmp_path, "r1", "a") == _digest(tmp_path, "r1", "a", defines=["SYNTHESIS"])


def test_syn_reruns_after_an_edit_inside_ifdef_synthesis(tmp_path, monkeypatch, capsys, slang) -> None:
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")
    rtl = tmp_path / "top.v"
    (tmp_path / "mock").mkdir()
    (tmp_path / "config.json").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.nop",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "top",
        "synthesis.inputs.input_files": [str(rtl)],
        "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
    }, cls=HammerJSONEncoder))

    def syn() -> str:
        capsys.readouterr()
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=["syn", "-o", str(tmp_path / "output.json"),
                                   "-p", str(tmp_path / "config.json"), "--obj_dir", str(tmp_path / "obj"),
                                   "--log", str(tmp_path / "log.txt")])
        assert cm.value.code == 0
        return capsys.readouterr().out

    rtl.write_text(GUARDED.replace("{SYN}", "r1").replace("{SIM}", "a"))
    syn()
    rtl.write_text(GUARDED.replace("{SYN}", "~r1").replace("{SIM}", "a"))
    assert "Database unchanged, can skip syn" not in syn()
    assert "Database unchanged, can skip syn" in syn()
