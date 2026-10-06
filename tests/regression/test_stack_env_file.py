import os
import shutil
import subprocess
import sys

import pytest

from hammer.shell import pd_store_cli, sledgehammer_cli

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs bash")

_CONN = "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Point SLEDGE_ENV_FILE at a file exporting the given variables; restores them afterwards."""
    def write(**exports) -> None:
        for name in (_CONN, "HAMMER_PG_HOST", *exports):
            # setenv first so monkeypatch records an undo even for an unset variable
            monkeypatch.setenv(name, "x")
            monkeypatch.delenv(name)
        f = tmp_path / "env.sh"
        f.write_text("".join(f"export {k}='{v}'\n" for k, v in exports.items()))
        monkeypatch.setenv("SLEDGE_ENV_FILE", str(f))
    return write


@pytest.fixture
def passthrough(tmp_path, monkeypatch):
    """main() for `sledgehammer dags list`; returns the env airflow would be exec'd with."""
    secrets = tmp_path / "secrets.gpg"
    secrets.write_text("")
    monkeypatch.setenv("SLEDGE_SECRETS_FILE", str(secrets))
    monkeypatch.delenv("SLEDGE_DRYRUN", raising=False)
    monkeypatch.setenv("AIRFLOW_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    real_run = subprocess.run

    def run(cmd, *a, **k):
        assert cmd[0] != "gpg", "decrypted the GPG secrets"
        return real_run(cmd, *a, **k)

    def execve(path, argv, env):
        raise SystemExit(env)

    monkeypatch.setattr(sledgehammer_cli.subprocess, "run", run)
    monkeypatch.setattr(sledgehammer_cli.os, "execve", execve)

    def go() -> dict:
        monkeypatch.setattr(sys, "argv", ["sledgehammer", "dags", "list"])
        with pytest.raises(SystemExit) as cm:
            sledgehammer_cli.main()
        return cm.value.code

    return go


def test_passthrough_reads_stack_env(env_file, passthrough, capsys) -> None:
    env_file(**{_CONN: "postgresql://stack/db", "HAMMER_PG_HOST": "stackhost"})
    env = passthrough()
    assert env[_CONN] == "postgresql://stack/db"
    assert env["HAMMER_PG_HOST"] == "stackhost"
    assert env["AIRFLOW_HOME"] == sledgehammer_cli.REPO
    assert "stack env from" in capsys.readouterr().err


def test_passthrough_export_beats_env_file(env_file, passthrough, monkeypatch) -> None:
    env_file(**{_CONN: "postgresql://stack/db"})
    monkeypatch.setenv(_CONN, "postgresql://mine/db")
    assert passthrough()[_CONN] == "postgresql://mine/db"


def test_studio_reads_stack_env(env_file, tmp_path, capsys) -> None:
    env_file(HAMMER_PG_HOST="fromfile")
    master = tmp_path / "master.json"
    master.write_text("{}")
    assert pd_store_cli.main(["stage-key", "synthesis", "--master", str(master)]) == 0
    out, err = capsys.readouterr()
    assert len(out.split()) == 1
    assert "stack env from" in err
    assert os.environ["HAMMER_PG_HOST"] == "fromfile"


def test_a_path_with_shell_syntax_is_sourced_not_run(tmp_path, monkeypatch, env_file) -> None:
    env_file(SLEDGE_STACK_PROBE="loaded")
    monkeypatch.chdir(tmp_path)
    canary = tmp_path / "INJECTED"
    weird = tmp_path / 'a"$(touch INJECTED)"b'
    weird.mkdir()
    target = weird / "env.sh"
    target.write_text("export SLEDGE_STACK_PROBE='loaded'\n")
    monkeypatch.setenv("SLEDGE_ENV_FILE", str(target))
    assert sledgehammer_cli._load_stack_env() == str(target)
    assert os.environ.get("SLEDGE_STACK_PROBE") == "loaded"
    assert not canary.exists()
