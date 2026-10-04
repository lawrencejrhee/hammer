import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hammer.shell import sledgehammer_cli


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """`sledgehammer run` as user 'u' from tmp_path/vlsi against a fake dags folder; never runs make."""
    import getpass
    monkeypatch.setattr(getpass, "getuser", lambda: "u")
    monkeypatch.setenv("AIRFLOW_HOME", str(tmp_path))
    dags = tmp_path / "dags"
    dags.mkdir()
    vlsi = tmp_path / "vlsi"
    vlsi.mkdir()
    monkeypatch.chdir(vlsi)
    calls = []

    def fake_airflow(*a, capture=True):
        calls.append(a)
        return 0, "", ""

    ran = []

    def no_subprocess(cmd, *a, **k):
        ran.append(cmd)
        raise AssertionError(f"ran {cmd}")

    monkeypatch.setattr(sledgehammer_cli, "_airflow", fake_airflow)
    monkeypatch.setattr(sledgehammer_cli, "_dags_folder", lambda: (str(dags), "test"))
    monkeypatch.setattr(sledgehammer_cli.subprocess, "run", no_subprocess)
    monkeypatch.setattr(sledgehammer_cli, "_load_stack_env", lambda: None)
    monkeypatch.setattr(sledgehammer_cli, "_load_secrets", lambda: None)

    def register(design: str, obj_dir) -> None:
        (dags / f"sledgehammer_{design}_u.py").write_text(f'OBJ_DIR = "{obj_dir}"\nforceall\n')

    def run(*args: str):
        """The triggered dag_id, or None."""
        calls.clear()
        sledgehammer_cli._cmd_run([*args, "--no-wait"])
        return next((c[2] for c in calls if c[:2] == ("dags", "trigger")), None)

    def main(*argv: str):
        calls.clear()
        monkeypatch.setattr(sys, "argv", ["sledgehammer", *argv, "--no-wait"])
        sledgehammer_cli.main()
        return next((c[2] for c in calls if c[:2] == ("dags", "trigger")), None)

    return SimpleNamespace(register=register, run=run, main=main, calls=calls, ran=ran, vlsi=vlsi, tmp=tmp_path)


class TestTopSelection:
    def test_unknown_top_exits_without_triggering(self, cli) -> None:
        cli.register("Bar", cli.vlsi / "build" / "Bar")
        with pytest.raises(SystemExit, match="no DAG registered for Foo"):
            cli.run("syn", "-t", "Foo")
        assert not any(c[:2] == ("dags", "trigger") for c in cli.calls)

    def test_known_top_triggers_that_dag(self, cli) -> None:
        cli.register("Bar", cli.vlsi / "build" / "Bar")
        cli.register("Foo", cli.tmp / "elsewhere" / "Foo")
        assert cli.run("syn", "-t", "Foo") == "sledgehammer_Foo_u"

    def test_bare_command_still_uses_cwd_dag(self, cli) -> None:
        cli.register("Bar", cli.vlsi / "build" / "Bar")
        assert cli.run("syn") == "sledgehammer_Bar_u"

    def test_legacy_spelling_unknown_top_exits(self, cli) -> None:
        cli.register("Bar", cli.vlsi / "build" / "Bar")
        with pytest.raises(SystemExit, match="no DAG registered for Foo"):
            cli.main("syn", "-t", "Foo")
        assert not any(c[:2] == ("dags", "trigger") for c in cli.calls)


class TestSameNameBuilds:
    def _builds(self, cli):
        for pdk in ("asap7", "sky130"):
            (cli.tmp / pdk / "gcd").mkdir(parents=True)
            (cli.tmp / pdk / "gcd" / "hammer_dag.py").write_text("")
        cli.register("gcd", cli.tmp / "asap7" / "gcd")

    def test_obj_dir_with_its_own_dag_is_not_served_by_another(self, cli) -> None:
        self._builds(cli)
        with pytest.raises(SystemExit, match="registered for .*asap7"):
            cli.run("syn", "--obj_dir", str(cli.tmp / "sky130" / "gcd"))
        assert not any(c[:2] == ("dags", "trigger") for c in cli.calls)

    def test_obj_dir_matching_the_dag_triggers(self, cli) -> None:
        self._builds(cli)
        assert cli.run("syn", "--obj_dir", str(cli.tmp / "asap7" / "gcd")) == "sledgehammer_gcd_u"

    def test_obj_dir_without_its_own_dag_still_triggers(self, cli) -> None:
        self._builds(cli)
        assert cli.run("syn", "--obj_dir", str(cli.tmp / "workspace" / "gcd")) == "sledgehammer_gcd_u"


class TestNoMakeProbe:
    def test_infer_obj_dir_never_runs_make(self, cli, monkeypatch) -> None:
        monkeypatch.delenv("OBJ_DIR", raising=False)
        (cli.vlsi / "Makefile").write_text("OBJ_DIR = build/x\n")
        with pytest.raises(SystemExit, match="No DAG yet"):
            cli.run("syn")
        assert cli.ran == []

    def test_two_cwd_dags_list_choices(self, cli) -> None:
        cli.register("A", cli.vlsi / "build" / "A")
        cli.register("B", cli.vlsi / "build" / "B")
        with pytest.raises(SystemExit, match="A, B. Pick one with -t"):
            cli.run("syn")
        assert not any(c[:2] == ("dags", "trigger") for c in cli.calls)

    def test_dag_generated_from_cwd_found_with_outside_obj_dir(self, cli) -> None:
        (cli.tmp / "dags" / "sledgehammer_Top_u.py").write_text(
            f'WORK_DIR = "{cli.vlsi}"\nOBJ_DIR = "{cli.tmp / "scratch" / "Top"}"\nforceall\n')
        assert cli.run("syn") == "sledgehammer_Top_u"

    def test_single_build_subdir_still_inferred(self, cli, monkeypatch) -> None:
        monkeypatch.delenv("OBJ_DIR", raising=False)
        (cli.vlsi / "build" / "Top").mkdir(parents=True)
        cli.register("Top", cli.tmp / "elsewhere" / "Top")
        assert cli.run("syn") == "sledgehammer_Top_u"
