import os
import sys

import pytest

from hammer.logging import HammerVLSILogging
from hammer.vlsi.submit_command import HammerSlurmSettings, HammerSlurmSubmitCommand

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a shell script as srun")


def _slurm(tmp_path, script: str, **settings) -> HammerSlurmSubmitCommand:
    srun = tmp_path / "srun"
    srun.write_text("#!/bin/sh\n" + script)
    srun.chmod(0o755)
    cmd = HammerSlurmSubmitCommand()
    cmd.settings = HammerSlurmSettings.from_setting({"srun_binary": str(srun), **settings})
    return cmd


@pytest.mark.parametrize("code", [0, 3])
def test_srun_exit_status_reaches_the_tool(tmp_path, code) -> None:
    cmd = _slurm(tmp_path, f'echo "ran $*"\nexit {code}\n')
    output, returncode = cmd.submit(["pegasus", "-drc"], dict(os.environ), HammerVLSILogging.context(""))
    assert output.splitlines() == ["ran pegasus -drc"]
    assert returncode == code
