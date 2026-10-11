import shlex
import types

import pytest

import hammer.shell.hammer_vlsi as hammer_vlsi
from hammer.shell import pd_store_cli
from hammer.vlsi import pd_store


@pytest.fixture(autouse=True)
def _unregistered(monkeypatch):
    monkeypatch.setattr(pd_store, "get_user_workspace", lambda *a, **k: None)
    # The resolver stamps these for the cache layer; setenv first so they are restored.
    for key in ("HAMMER_AIRFLOW_DAG_ID", "HAMMER_AIRFLOW_RUN_ID", "HAMMER_AIRFLOW_TRIGGERING_USER",
                "HAMMER_AIRFLOW_DESIGN", "OBJ_DIR"):
        monkeypatch.setenv(key, "")
    for key in ("HAMMER_NO_PER_USER_WORKSPACE", "HAMMER_WORKSPACE"):
        monkeypatch.delenv(key, raising=False)


def _remedy(user: str, conf: dict, tmp_path) -> list:
    context = {"dag_run": types.SimpleNamespace(conf=conf, dag_id="sledgehammer_Top_alice", run_id="r1",
                                                triggering_user_name=user),
               "params": {}}
    with pytest.raises(hammer_vlsi.WorkspaceNotRegistered) as err:
        hammer_vlsi._resolve_workspace_obj_dir(context, "Top", default_obj_dir=str(tmp_path / "alice"),
                                               gen_user="alice", claim=False)
    words = shlex.split(str(err.value).split("Register one first:", 1)[1])
    assert words[0] == "studio"
    return words[1:]


@pytest.mark.parametrize("name", ["iter2", "my iter"])
@pytest.mark.parametrize("user", ["alice", "bob"])
def test_the_remedy_registers_the_named_workspace(user, name, tmp_path) -> None:
    args = pd_store_cli._build_parser().parse_args(_remedy(user, {"workspace": name}, tmp_path))
    assert (args.username, args.name) == (user, name)


def test_the_remedy_for_the_default_workspace_names_no_workspace(tmp_path) -> None:
    words = _remedy("bob", {}, tmp_path)
    assert "--name" not in words
    assert pd_store_cli._build_parser().parse_args(words).name == "default"
