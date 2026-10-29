import json

import pytest

from hammer.vlsi import CLIDriver, rtl_check


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(rtl_check, "digest_units", lambda *a, **k: ("0" * 64, []))
    monkeypatch.setattr(rtl_check, "digest_files", lambda *a, **k: ("0" * 64, []))
    monkeypatch.delenv("HAMMER_PD_CACHE", raising=False)
    monkeypatch.setenv("HAMMER_DB_CHECKPOINTS", "0")


def _run(tmp_path, action: str, config: str) -> dict:
    out = tmp_path / f"{action}-out.json"
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=[action, "-p", config, "-o", str(out), "--obj_dir", str(tmp_path / "obj"),
                               "--log", str(tmp_path / "log.txt")])
    assert cm.value.code == 0, action
    return json.loads(out.read_text())


def _par_out(tmp_path, **extra) -> str:
    par_out = {"par.outputs.output_netlist": "/run/dummy.lvs.v", "par.inputs.top_module": "dummy",
               "par.inputs.post_synth_sdc": None, "par.outputs.spefs": [],
               "vlsi.core.technology": "hammer.technology.nop"}
    par_out.update(extra)
    (tmp_path / "par-out.json").write_text(json.dumps(par_out))
    return str(tmp_path / "par-out.json")


@pytest.mark.parametrize("physical", [None, "", "None"])
def test_par_to_timing_falls_back_to_the_netlist_without_a_physical_one(tmp_path, physical) -> None:
    """A missing physical netlist, or the string "None" a par tool without one records, is not a file."""
    extra = {} if physical is None else {"par.outputs.output_physical_netlist": physical}
    timing = _run(tmp_path, "par-to-timing", _par_out(tmp_path, **extra))
    assert timing["timing.inputs.input_files"] == ["/run/dummy.lvs.v"]


def test_par_to_timing_prefers_a_real_physical_netlist(tmp_path) -> None:
    timing = _run(tmp_path, "par-to-timing",
                  _par_out(tmp_path, **{"par.outputs.output_physical_netlist": "/run/dummy.physical.v"}))
    assert timing["timing.inputs.input_files"] == ["/run/dummy.physical.v"]


def test_timing_after_a_mock_par_reads_its_netlist(tmp_path) -> None:
    rtl = tmp_path / "top.v"
    rtl.write_text("module dummy(input a, output b); assign b = a; endmodule\n")
    (tmp_path / "config.json").write_text(json.dumps({
        "vlsi.core.technology": "hammer.technology.nop",
        "vlsi.core.synthesis_tool": "hammer.synthesis.mocksynth",
        "vlsi.core.par_tool": "hammer.par.mockpar",
        "vlsi.inputs.hierarchical.config_source": "none",
        "vlsi.technology.extra_macro_sizes": [],
        "synthesis.inputs.top_module": "dummy",
        "synthesis.inputs.input_files": [str(rtl)],
    }))
    obj = tmp_path / "obj"
    _run(tmp_path, "syn", str(tmp_path / "config.json"))
    _run(tmp_path, "syn-to-par", str(obj / "syn-rundir" / "syn-output-full.json"))
    _run(tmp_path, "par", str(tmp_path / "syn-to-par-out.json"))
    par_out = json.loads((obj / "par-rundir" / "par-output-full.json").read_text())
    assert par_out["par.outputs.output_physical_netlist"] == "None"
    # mockpar writes no SPEF or SDC, which the bridge requires.
    par_out.update({"par.outputs.spefs": [], "par.inputs.post_synth_sdc": None})
    (tmp_path / "mock-par-out.json").write_text(json.dumps(par_out))
    timing = _run(tmp_path, "par-to-timing", str(tmp_path / "mock-par-out.json"))
    assert timing["timing.inputs.input_files"] == [par_out["par.outputs.output_netlist"]]
