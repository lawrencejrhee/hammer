import json
from pathlib import Path
from typing import List

from hammer.config import HammerJSONEncoder
from hammer.synthesis.genus import Genus
from hammer.vlsi import HammerDriver


def _init_environment(tmp_path: Path, monkeypatch, **extra) -> List[str]:
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule\n")
    cfg = {
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.genus",
        "synthesis.inputs.top_module": "top",
        "synthesis.inputs.input_files": [str(rtl)],
    }
    cfg.update(extra)
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
    return tool.output


def test_no_power_intent_without_a_power_spec(tmp_path, monkeypatch) -> None:
    out = "\n".join(_init_environment(tmp_path, monkeypatch))
    assert "apply_power_intent" not in out
    assert "read_power_intent" not in out


def test_power_intent_order_with_a_power_spec(tmp_path, monkeypatch) -> None:
    out = _init_environment(tmp_path, monkeypatch, **{
        "vlsi.inputs.power_spec_mode": "manual",
        "vlsi.inputs.power_spec_type": "cpf",
        "vlsi.inputs.power_spec_contents": "set_cpf_version 1.0e",
    })
    read = next(i for i, l in enumerate(out) if "read_power_intent" in l)
    apply = next(i for i, l in enumerate(out) if l.startswith("apply_power_intent -summary"))
    commit = next(i for i, l in enumerate(out) if "commit_power_intent" in l)
    assert read < apply < commit
