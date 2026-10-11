import os
import shutil
import sys

import pytest

from hammer.shell import sledgehammer_cli

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def load_env_file(tmp_path, monkeypatch):
    """Write env.sh into a directory, load it as the stack env file and return the environment; restores os.environ."""
    saved = dict(os.environ)
    monkeypatch.chdir(tmp_path)
    for name in ("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "HAMMER_PG_HOST"):
        os.environ.pop(name, None)

    def load(text: str, directory=tmp_path) -> dict:
        f = directory / "env.sh"
        f.write_text(text)
        os.environ["SLEDGE_ENV_FILE"] = str(f)
        assert sledgehammer_cli._load_stack_env() == str(f)
        return dict(os.environ)

    yield load
    os.environ.clear()
    os.environ.update(saved)


def test_env_file_sees_no_arguments(load_env_file, tmp_path) -> None:
    env = load_env_file('export STACK_ENV_ARGC="$#" STACK_ENV_ARG1="${1-none}"\n')
    assert env["STACK_ENV_ARGC"] == "0"
    assert env["STACK_ENV_ARG1"] == "none"
    assert str(tmp_path / "env.sh") not in [v for k, v in env.items() if k != "SLEDGE_ENV_FILE"]


def test_a_helper_sourced_without_arguments_sees_none(load_env_file, tmp_path) -> None:
    (tmp_path / "activate").write_text(
        'conda() { [ "$1" = activate ] && [ $# -eq 1 ] && export STACK_ENV_CONDA_ENV=base; }\n'
        'export STACK_ENV_HELPER_ARGC="$#"\n'
        'conda activate "$@"\n')
    env = load_env_file('source "$(dirname "${BASH_SOURCE[0]}")/activate"\n')
    assert env["STACK_ENV_HELPER_ARGC"] == "0"
    assert env.get("STACK_ENV_CONDA_ENV") == "base"


def test_a_path_with_shell_syntax_sees_no_arguments(load_env_file, tmp_path) -> None:
    weird = tmp_path / 'a"$(touch INJECTED)"b'
    weird.mkdir()
    env = load_env_file('export STACK_ENV_ARGC="$#"\n', weird)
    assert env["STACK_ENV_ARGC"] == "0"
    assert not (tmp_path / "INJECTED").exists()
