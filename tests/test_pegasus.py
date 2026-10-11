import json
import os
import stat
from types import SimpleNamespace

import pytest

from hammer.drc.pegasus import PegasusDRC
from hammer.logging.test import HammerLoggingCaptureContext
from hammer.lvs.pegasus import PegasusLVS
from hammer.vlsi import CLIDriver

FAKE_PEGASUS = """\
#!/bin/sh
ctl=""
while [ $# -gt 0 ]; do
  [ "$1" = "-control" ] && ctl="$2"
  shift
done
if [ "$FAKE_PEGASUS_MODE" = "ok" ]; then
  sed -n -e 's/.*lvs_report_file "\\([^"]*\\)".*/\\1/p' \\
         -e 's/.*report_summary -[a-z]* "\\([^"]*\\)".*/\\1/p' "$ctl" |
    while read -r f; do echo "completed" > "$f"; echo "#####  Run Result   :   MATCH" > "$f.cls"; done
  exit 0
fi
echo "Execution aborted.  Exiting with status 1."
exit 1
"""


@pytest.fixture(autouse=True)
def _one_rule_deck(tmpdir, monkeypatch):
    deck = os.path.join(tmpdir, "rules.pvl")
    open(deck, "w").close()
    monkeypatch.setattr(PegasusDRC, "get_drc_decks", lambda self: [SimpleNamespace(path=deck)])
    monkeypatch.setattr(PegasusLVS, "get_lvs_decks", lambda self: [SimpleNamespace(path=deck)])


def _run(tmpdir, kind: str) -> object:
    pegasus = os.path.join(tmpdir, "pegasus")
    with open(pegasus, "w") as f:
        f.write(FAKE_PEGASUS)
    os.chmod(pegasus, os.stat(pegasus).st_mode | stat.S_IXUSR)
    layout = os.path.join(tmpdir, "top.gds")
    netlist = os.path.join(tmpdir, "top.v")
    for p in (layout, netlist):
        open(p, "w").close()
    cfg = os.path.join(tmpdir, "cfg.json")
    with open(cfg, "w") as f:
        json.dump({
            "vlsi.core.technology": "hammer.technology.nop",
            f"vlsi.core.{kind}_tool": f"hammer.{kind}.pegasus",
            f"{kind}.pegasus.pegasus_bin": pegasus,
            f"{kind}.inputs.top_module": "top",
            f"{kind}.inputs.layout_file": layout,
            "lvs.inputs.schematic_files": [netlist],
        }, f)
    with pytest.raises(SystemExit) as cm:
        CLIDriver().main(args=[
            kind,
            "-p", cfg,
            "--obj_dir", os.path.join(tmpdir, "obj"),
            "--log", os.path.join(tmpdir, "log.txt"),
        ])
    return cm.value.code


def _results_file(tmpdir, kind: str) -> str:
    return os.path.join(tmpdir, "obj", f"{kind}-rundir", f"top.{kind}_results")


@pytest.mark.parametrize("kind", ["drc", "lvs"])
class TestPegasusExitStatus:
    def test_completed_run_passes(self, tmpdir, kind, monkeypatch) -> None:
        monkeypatch.setenv("FAKE_PEGASUS_MODE", "ok")
        assert _run(tmpdir, kind) == 0
        assert os.path.isfile(_results_file(tmpdir, kind))

    def test_aborted_run_fails(self, tmpdir, kind, monkeypatch) -> None:
        monkeypatch.setenv("FAKE_PEGASUS_MODE", "abort")
        with HammerLoggingCaptureContext() as c:
            assert _run(tmpdir, kind) != 0
        assert c.log_contains(f"Pegasus {kind.upper()} did not complete (exit status 1")

    def test_stale_results_do_not_mask_an_abort(self, tmpdir, kind, monkeypatch) -> None:
        monkeypatch.setenv("FAKE_PEGASUS_MODE", "ok")
        assert _run(tmpdir, kind) == 0
        monkeypatch.setenv("FAKE_PEGASUS_MODE", "abort")
        assert _run(tmpdir, kind) != 0
        assert not os.path.exists(_results_file(tmpdir, kind))
