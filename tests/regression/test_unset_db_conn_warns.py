"""sledgehammer names the secrets file and airflow.cfg on stderr when no metadata DB connection is set."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hammer.shell import sledgehammer_cli

_CONN = "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    """SLEDGE_SECRETS_FILE points at tmp_path/secrets.gpg, absent until decrypts_to() writes it with a fake gpg."""
    path = tmp_path / "secrets.gpg"
    cfg = tmp_path / "home" / "airflow.cfg"
    monkeypatch.setenv("SLEDGE_SECRETS_FILE", str(path))
    monkeypatch.setenv("AIRFLOW_HOME", str(cfg.parent))
    monkeypatch.delenv("AIRFLOW_CONFIG", raising=False)
    for name in (_CONN, "HAMMER_PG_PASSWORD"):
        # setenv first so monkeypatch restores even a variable that was unset
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)

    def no_subprocess(cmd, *a, **k):
        raise AssertionError(f"ran {cmd}")

    monkeypatch.setattr(sledgehammer_cli.subprocess, "run", no_subprocess)

    def decrypts_to(text: str) -> None:
        path.write_text("")

        def gpg(cmd, *a, **k):
            assert cmd[0] == "gpg"
            return subprocess.CompletedProcess(cmd, 0, text.encode(), b"")

        monkeypatch.setattr(sledgehammer_cli.subprocess, "run", gpg)

    return SimpleNamespace(path=path, cfg=cfg, decrypts_to=decrypts_to)


def test_a_missing_secrets_file_warns_with_its_path_and_the_airflow_cfg(secrets, capsys) -> None:
    sledgehammer_cli._load_secrets()
    out, err = capsys.readouterr()
    assert out == ""
    assert len(err.splitlines()) == 1
    assert str(secrets.path) in err
    assert str(secrets.cfg) in err


def test_a_secrets_file_warns_only_when_it_lacks_the_conn(secrets, capsys) -> None:
    secrets.decrypts_to(f"export {_CONN}='postgresql://u@h/db'\nHAMMER_PG_PASSWORD=pw\n")
    sledgehammer_cli._load_secrets()
    assert os.environ[_CONN] == "postgresql://u@h/db"
    assert capsys.readouterr().err == ""

    del os.environ[_CONN]
    secrets.decrypts_to("HAMMER_PG_PASSWORD=pw\n")
    sledgehammer_cli._load_secrets()
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert str(secrets.path) in err
    assert str(secrets.cfg) in err


def test_an_exported_conn_is_silent(secrets, monkeypatch, capsys) -> None:
    monkeypatch.setenv(_CONN, "postgresql://u@h/db")
    sledgehammer_cli._load_secrets()
    assert capsys.readouterr().err == ""

    monkeypatch.delenv(_CONN)
    sledgehammer_cli._load_secrets()
    assert str(secrets.cfg) in capsys.readouterr().err


def test_the_warning_names_airflow_config_when_it_is_set(secrets, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AIRFLOW_CONFIG", str(tmp_path / "elsewhere.cfg"))
    sledgehammer_cli._load_secrets()
    err = capsys.readouterr().err
    assert str(tmp_path / "elsewhere.cfg") in err
    assert str(secrets.cfg) not in err


def test_db_check_passes_through_to_airflow_with_the_warning(secrets, tmp_path, monkeypatch, capsys) -> None:
    airflow = tmp_path / "bin" / "airflow"
    airflow.parent.mkdir()
    airflow.write_text("#!/bin/sh\nexit 0\n")
    airflow.chmod(0o755)
    monkeypatch.setattr(sledgehammer_cli, "_venv_bin", lambda name: str(tmp_path / "bin" / name))
    monkeypatch.setattr(sledgehammer_cli, "STACK_ENV_HOME", str(tmp_path / "no-env.sh"))
    monkeypatch.delenv("SLEDGE_ENV_FILE", raising=False)
    monkeypatch.delenv("SLEDGE_DRYRUN", raising=False)
    monkeypatch.chdir(tmp_path)
    execs = []

    def execve(path, argv, env):
        execs.append((path, argv))
        raise SystemExit(0)

    monkeypatch.setattr(sledgehammer_cli.os, "execve", execve)
    monkeypatch.setattr(sys, "argv", ["sledgehammer", "db", "check"])
    with pytest.raises(SystemExit):
        sledgehammer_cli.main()
    assert execs == [(str(airflow), [str(airflow), "db", "check"])]
    out, err = capsys.readouterr()
    assert out == ""
    assert len(err.splitlines()) == 1
    assert str(secrets.path) in err
    assert os.path.join(sledgehammer_cli.REPO, "airflow.cfg") in err
