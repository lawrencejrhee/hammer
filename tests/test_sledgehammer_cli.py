import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hammer.shell import sledgehammer_cli


class TestAirflowEnv:
    def test_conda_library_dirs_are_dropped(self, monkeypatch) -> None:
        monkeypatch.setenv("CONDA_PREFIX", "/opt/envs/cy")
        monkeypatch.setenv("LD_LIBRARY_PATH", os.pathsep.join([
            "/home/u/chipyard/.conda-env/lib", "/opt/envs/cy/lib", "/home/u/miniforge3/lib",
            "/home/u/libnsl_local/usr/lib64"]))
        assert sledgehammer_cli._airflow_env()["LD_LIBRARY_PATH"] == "/home/u/libnsl_local/usr/lib64"

    def test_only_conda_entries_unsets_the_variable(self, monkeypatch) -> None:
        monkeypatch.delenv("CONDA_PREFIX", raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/x/Conda/lib")
        assert "LD_LIBRARY_PATH" not in sledgehammer_cli._airflow_env()

    def test_airflow_subprocess_gets_the_clean_env(self, monkeypatch) -> None:
        monkeypatch.delenv("CONDA_PREFIX", raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", os.pathsep.join(["/x/conda/lib", "/usr/lib64"]))
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(sledgehammer_cli.subprocess, "run", fake_run)
        sledgehammer_cli._airflow("version")
        assert seen["env"]["LD_LIBRARY_PATH"] == "/usr/lib64"


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs bash")
def test_launch_sources_venv_sh_from_the_checkout_and_forwards_args(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "hammer"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "activate").write_text("")
    (repo / "venv.sh").write_text("source ./.venv/bin/activate\nexport FROM_VENV_SH=1\n")
    launcher = repo / "launcher.py"
    launcher.write_text(
        "import json, os, sys\n"
        "print(json.dumps([os.getcwd(), os.environ.get('FROM_VENV_SH'), sys.argv[1:]]))\n")
    elsewhere = tmp_path / "chipyard" / "vlsi"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sledgehammer_cli, "REPO", str(repo))
    monkeypatch.setattr(sledgehammer_cli, "LAUNCHER", str(launcher))
    monkeypatch.setattr(sledgehammer_cli.sys, "argv", ["sledgehammer", "up", "--port", "a b"])
    monkeypatch.delenv("SLEDGE_DRYRUN", raising=False)
    monkeypatch.setenv("SLEDGE_2FA", "1")
    monkeypatch.setenv("AIRFLOW_HOME", str(tmp_path))
    captured = []

    def fake_execvp(file, args):
        captured.append((file, args))
        raise SystemExit(0)

    monkeypatch.setattr(sledgehammer_cli.os, "execvp", fake_execvp)
    with pytest.raises(SystemExit):
        sledgehammer_cli.main()
    file, args = captured[0]
    assert file == "bash"
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    cwd, from_venv_sh, argv = json.loads(out.strip().splitlines()[-1])
    assert os.path.realpath(cwd) == os.path.realpath(repo)
    assert from_venv_sh == "1"
    assert argv == ["--port", "a b"]
