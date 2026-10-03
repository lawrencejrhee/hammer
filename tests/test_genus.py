import json
from pathlib import Path
from typing import List, Optional

from hammer.config import HammerJSONEncoder
from hammer.synthesis.genus import Genus
from hammer.vlsi import HammerDriver


def _read_hdl(tmp_path: Path, monkeypatch, defines: Optional[List[str]] = None) -> str:
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule\n")
    cfg = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.genus",
        "synthesis.inputs.top_module": "top",
        "synthesis.inputs.input_files": [str(rtl)],
    }
    if defines is not None:
        cfg["synthesis.inputs.defines"] = defines
    cfg_path = tmp_path / "project.json"
    cfg_path.write_text(json.dumps(cfg, cls=HammerJSONEncoder))
    driver = HammerDriver(HammerDriver.get_default_driver_options()._replace(
        project_configs=[str(cfg_path)], obj_dir=str(tmp_path / "obj"), log_file=str(tmp_path / "log.txt")))
    run_dir = tmp_path / "syn-rundir"
    run_dir.mkdir()
    assert driver.load_synthesis_tool(run_dir=str(run_dir))
    tool = driver.syn_tool
    assert isinstance(tool, Genus)
    monkeypatch.setattr(tool, "generate_mmmc_script", lambda: "")
    assert tool.init_environment()
    return next(line for line in tool.output if line.startswith("read_hdl"))


def test_genus_read_hdl_passes_defines(tmp_path, monkeypatch) -> None:
    line = _read_hdl(tmp_path, monkeypatch, ["WIDTH=8", "FAST"])
    assert "read_hdl -define WIDTH=8 -define FAST -sv {" in line
    assert str(tmp_path / "top.v") in line


def test_genus_read_hdl_without_defines(tmp_path, monkeypatch) -> None:
    line = _read_hdl(tmp_path, monkeypatch)
    assert "-define" not in line
    assert str(tmp_path / "top.v") in line
