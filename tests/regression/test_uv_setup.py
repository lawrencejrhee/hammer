import os
import re
import shutil
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "uv_setup.sh")


def _text() -> str:
    with open(SCRIPT) as f:
        return f.read().replace("\r\n", "\n")


def test_no_fixed_tmp_paths() -> None:
    assert not re.search(r"(?<![\w$])/tmp/", _text())


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_parses() -> None:
    subprocess.run(["bash", "-n"], input=_text(), text=True, check=True)
