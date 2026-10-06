import os
import shutil
import subprocess
import sys

import pytest

VENV_SH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "venv.sh")
# uv's real activate script finds itself in bash and zsh; the stand-in bakes its path in.
ACTIVATE = 'export VIRTUAL_ENV="{venv}"\nexport PATH="$VIRTUAL_ENV/bin:$PATH"\n'

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                                reason="venv.sh needs bash")


def _checkout(tmp_path, with_venv=True):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    with open(VENV_SH) as src:
        (checkout / "venv.sh").write_text(src.read().replace("\r\n", "\n"))
    if with_venv:
        (checkout / ".venv" / "bin").mkdir(parents=True)
        (checkout / ".venv" / "bin" / "activate").write_text(ACTIVATE.format(venv=checkout / ".venv"))
    return checkout


# venv.sh is sourced from people's login shells, and zsh is the macOS default.
SHELLS = [s for s in ("bash", "zsh") if shutil.which(s)]


@pytest.fixture(params=SHELLS)
def shell(request):
    return request.param


def _source(tmp_path, cwd, script, shell="bash", **env):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cmd = (f'source {script}; echo "RC=$?"; echo "AH=${{AIRFLOW_HOME:-}}"; '
           'echo "VE=${VIRTUAL_ENV:-}"; echo "PATH=$PATH"; echo "LD=${LD_LIBRARY_PATH:-}"')
    argv = [shell, "-f", "-c", cmd] if shell == "zsh" else [shell, "-c", cmd]
    res = subprocess.run(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                         capture_output=True, text=True,
                         env={"HOME": str(home), "PATH": "/usr/bin:/bin", **env})
    out = dict(line.split("=", 1) for line in res.stdout.splitlines()
               if line.split("=", 1)[0] in ("RC", "AH", "VE", "PATH", "LD"))
    return out, res.stderr


def test_sourcing_from_another_directory_activates_the_checkout(tmp_path, shell) -> None:
    checkout = _checkout(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    out, _ = _source(tmp_path, tmp_path / "elsewhere", checkout / "venv.sh", shell=shell)
    assert out["AH"] == str(checkout)
    assert out["VE"] == str(checkout / ".venv")


def test_venv_bin_precedes_local_bin(tmp_path, shell) -> None:
    checkout = _checkout(tmp_path)
    out, _ = _source(tmp_path, checkout, "./venv.sh", shell=shell)
    path = out["PATH"].split(":")
    assert path.index(str(checkout / ".venv" / "bin")) < path.index(str(tmp_path / "home" / ".local" / "bin"))


def test_missing_venv_fails_loudly(tmp_path, shell) -> None:
    checkout = _checkout(tmp_path, with_venv=False)
    out, err = _source(tmp_path, tmp_path, checkout / "venv.sh", shell=shell)
    assert out["RC"] != "0"
    assert "no venv at" in err
    assert out["AH"] == ""


def test_relative_source_with_cdpath_set(tmp_path, shell) -> None:
    checkout = _checkout(tmp_path)
    out, _ = _source(tmp_path, tmp_path, "checkout/venv.sh", CDPATH="/tmp", shell=shell)
    assert out["AH"] == str(checkout)


def test_conda_entries_leave_the_rest_of_ld_library_path(tmp_path, shell) -> None:
    checkout = _checkout(tmp_path)
    out, _ = _source(tmp_path, checkout, "./venv.sh", shell=shell,
                     LD_LIBRARY_PATH="/opt/keep:/x/miniconda3/lib:/opt/keep2")
    assert out["LD"] == "/opt/keep:/opt/keep2"
