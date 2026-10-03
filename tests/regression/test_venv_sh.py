import os
import shutil
import subprocess
import sys

import pytest

VENV_SH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "venv.sh")
ACTIVATE = ('export VIRTUAL_ENV="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"\n'
            'export PATH="$VIRTUAL_ENV/bin:$PATH"\n')

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                                reason="venv.sh needs bash")


def _checkout(tmp_path, with_venv=True):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    with open(VENV_SH) as src:
        (checkout / "venv.sh").write_text(src.read().replace("\r\n", "\n"))
    if with_venv:
        (checkout / ".venv" / "bin").mkdir(parents=True)
        (checkout / ".venv" / "bin" / "activate").write_text(ACTIVATE)
    return checkout


def _source(tmp_path, cwd, script, **env):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cmd = (f'source {script}; echo "RC=$?"; echo "AH=${{AIRFLOW_HOME:-}}"; '
           'echo "VE=${VIRTUAL_ENV:-}"; echo "PATH=$PATH"')
    res = subprocess.run(["bash", "-c", cmd], cwd=str(cwd), stdin=subprocess.DEVNULL,
                         capture_output=True, text=True,
                         env={"HOME": str(home), "PATH": "/usr/bin:/bin", **env})
    out = dict(line.split("=", 1) for line in res.stdout.splitlines()
               if line.split("=", 1)[0] in ("RC", "AH", "VE", "PATH"))
    return out, res.stderr


def test_sourcing_from_another_directory_activates_the_checkout(tmp_path) -> None:
    checkout = _checkout(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    out, _ = _source(tmp_path, tmp_path / "elsewhere", checkout / "venv.sh")
    assert out["AH"] == str(checkout)
    assert out["VE"] == str(checkout / ".venv")


def test_venv_bin_precedes_local_bin(tmp_path) -> None:
    checkout = _checkout(tmp_path)
    out, _ = _source(tmp_path, checkout, "./venv.sh")
    path = out["PATH"].split(":")
    assert path.index(str(checkout / ".venv" / "bin")) < path.index(str(tmp_path / "home" / ".local" / "bin"))


def test_missing_venv_fails_loudly(tmp_path) -> None:
    checkout = _checkout(tmp_path, with_venv=False)
    out, err = _source(tmp_path, tmp_path, checkout / "venv.sh")
    assert out["RC"] != "0"
    assert "no venv at" in err
    assert out["AH"] == ""


def test_relative_source_with_cdpath_set(tmp_path) -> None:
    checkout = _checkout(tmp_path)
    out, _ = _source(tmp_path, tmp_path, "checkout/venv.sh", CDPATH="/tmp")
    assert out["AH"] == str(checkout)
