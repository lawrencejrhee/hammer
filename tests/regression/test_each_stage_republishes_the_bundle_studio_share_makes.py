import json

import pytest

from hammer.vlsi import design_share

from tests.regression.test_pulled_design_hits_the_owners_cache import FakeDB, db, tool_runs  # noqa: F401
from tests.regression.test_an_unchanged_rerun_does_not_republish import _run_all, _shared_site_build

CHANGES = {
    "rtl": None,
    "par": ("par.blockage_spacing", 2.5),
    "drc": ("drc.inputs.additional_drc_text", "# checked by the owner"),
    "lvs": ("lvs.inputs.additional_lvs_text", "# checked by the owner"),
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_each_stage_republishes_the_bundle_studio_share_makes(change, tmp_path, db, tool_runs,
                                                              monkeypatch) -> None:
    alice, obj_a, uploads, packs = _shared_site_build(tmp_path, db, monkeypatch)
    if CHANGES[change] is None:
        (alice / "rtl" / "top.v").write_text('`include "defs.vh"\n'
                                            'module dummy(input a, output b); assign b = ~a; endmodule\n')
    else:
        key, value = CHANGES[change]
        config = json.loads((alice / "design.yml").read_text())
        config[key] = value
        (alice / "design.yml").write_text(json.dumps(config, indent=4))
    dag_file = str(obj_a / "hammer_dag.py")
    _run_all(dag_file, tmp_path)
    assert len(packs) == 1
    assert uploads == [design_share.bundle_from_dag(dag_file, owner="alice").bundle_sha]
