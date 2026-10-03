import json
import os
from pathlib import Path
from typing import List

import pytest

from hammer.config import HammerJSONEncoder
from hammer.synthesis.genus import Genus
from hammer.synthesis.yosys import YosysSynth
from hammer.vlsi import CLIDriver, HammerDriver, pd_store, rtl_check

TOP_SV = """\
`include "defs.svh"
`include <width.svh>
module top(input logic [`W-1:0] a, output logic [`W-1:0] y);
  assign y = a ^ `MASK;
endmodule
"""


@pytest.fixture
def slang() -> None:
    try:
        rtl_check._checked_binary()
    except rtl_check.SlangNotFound as e:
        pytest.skip(f"needs slang {rtl_check.SLANG_VERSION}: {e}")


def _design(tmp_path: Path) -> List[str]:
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "defs.svh").write_text("`define MASK 4'h5\n")
    (inc / "width.svh").write_text("`define W 4\n")
    rtl = tmp_path / "top.sv"
    rtl.write_text(TOP_SV)
    return [str(rtl), str(inc)]


class TestFingerprint:
    def test_quoted_and_angle_includes_resolve(self, tmp_path, slang) -> None:
        rtl, inc = _design(tmp_path)
        first = rtl_check.digest_files([rtl], include_dirs=[inc], top_module="top")[0]
        (tmp_path / "inc" / "width.svh").write_text("`define W 8\n")
        assert rtl_check.digest_files([rtl], include_dirs=[inc], top_module="top")[0] != first

    def test_cache_key_fingerprint_agrees(self, tmp_path, slang) -> None:
        rtl, inc = _design(tmp_path)
        assert pd_store.compute_rtl_fingerprint([rtl], top_module="top", include_dirs=[inc]) == \
            rtl_check.digest_files([rtl], include_dirs=[inc], top_module="top")[0]

    def test_driver_passes_include_dirs(self, tmp_path, slang) -> None:
        rtl, inc = _design(tmp_path)
        (tmp_path / "mock").mkdir()
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({
            "vlsi.core.technology": "hammer.technology.nop",
            "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
            "synthesis.inputs.top_module": "top",
            "synthesis.inputs.input_files": [rtl],
            "synthesis.inputs.include_dirs": [inc],
            "synthesis.mocksynth.temp_folder": str(tmp_path / "mock"),
        }))
        obj = tmp_path / "obj"
        with pytest.raises(SystemExit) as cm:
            CLIDriver().main(args=[
                "syn", "-p", str(cfg), "--obj_dir", str(obj),
                "--syn_rundir", str(tmp_path / "syn"),
                "-o", str(tmp_path / "output.json"),
                "--log", str(tmp_path / "log.txt"),
            ])
        assert cm.value.code == 0
        stored = json.loads((obj / "master_database.json").read_text())["vlsi.rtl_fingerprint_sha256"]
        assert stored == rtl_check.digest_files([rtl], include_dirs=[inc], top_module="top")[0]


def _syn_tool(tmp_path: Path, monkeypatch, tool: str, **extra):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top(input clk); endmodule\n")
    cfg = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": f"hammer.synthesis.{tool}",
        "synthesis.inputs.top_module": "top",
        "synthesis.inputs.input_files": [str(rtl)],
        "vlsi.inputs.clocks": [{"name": "clk", "period": "1 ns", "uncertainty": "0.1 ns"}],
        "vlsi.inputs.mmmc_corners": [{"name": "tt", "type": "extra", "voltage": "1.8 V", "temp": "25 C"}],
    }
    cfg.update(extra)
    cfg_path = tmp_path / "project.json"
    cfg_path.write_text(json.dumps(cfg, cls=HammerJSONEncoder))
    driver = HammerDriver(HammerDriver.get_default_driver_options()._replace(
        project_configs=[str(cfg_path)], obj_dir=str(tmp_path / "obj"), log_file=str(tmp_path / "log.txt")))
    run_dir = tmp_path / "syn-rundir"
    run_dir.mkdir()
    assert driver.load_synthesis_tool(run_dir=str(run_dir))
    syn = driver.syn_tool
    if isinstance(syn, Genus):
        monkeypatch.setattr(syn, "generate_mmmc_script", lambda: "")
    assert syn.init_environment()
    return syn.output


class TestSynthesisTools:
    def test_genus_search_path_precedes_read_hdl(self, tmp_path, monkeypatch) -> None:
        out = _syn_tool(tmp_path, monkeypatch, "genus", **{"synthesis.inputs.include_dirs": ["inc"]})
        search = next(i for i, l in enumerate(out) if "init_hdl_search_path" in l)
        read = next(i for i, l in enumerate(out) if l.startswith("read_hdl"))
        assert search < read
        assert os.path.join(os.getcwd(), "inc") in out[search]

    def test_genus_unchanged_without_include_dirs(self, tmp_path, monkeypatch) -> None:
        assert not any("init_hdl_search_path" in l for l in _syn_tool(tmp_path, monkeypatch, "genus"))

    def test_yosys_reads_with_include_flags(self, tmp_path, monkeypatch) -> None:
        out = _syn_tool(tmp_path, monkeypatch, "yosys", **{"synthesis.inputs.include_dirs": ["inc"]})
        reads = [l for l in out if l.startswith("read_verilog")]
        assert reads and all(f"-I{os.path.join(os.getcwd(), 'inc')} " in l for l in reads)

    def test_yosys_unchanged_without_include_dirs(self, tmp_path, monkeypatch) -> None:
        reads = [l for l in _syn_tool(tmp_path, monkeypatch, "yosys") if l.startswith("read_verilog")]
        assert reads and all(l.startswith("read_verilog -sv /") for l in reads)
