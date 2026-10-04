from hammer.shell import pd_store_cli
from hammer.vlsi import pd_store


def _fake(monkeypatch, root):
    calls = []

    def get(username, workspace_name="default", **kw):
        calls.append(kw)
        return root

    monkeypatch.setattr(pd_store, "get_user_workspace", get)
    return calls


def test_missing_workspace_is_a_read_only_error(monkeypatch, capsys) -> None:
    calls = _fake(monkeypatch, None)
    assert pd_store_cli.main(["workspace-show", "alice"]) == 1
    assert calls == [{"auto_register": False}]
    out, err = capsys.readouterr()
    assert out == "" and "workspace-set alice" in err


def test_registered_root_is_printed(monkeypatch, capsys) -> None:
    _fake(monkeypatch, "/scratch/alice/build")
    assert pd_store_cli.main(["workspace-show", "alice"]) == 0
    assert capsys.readouterr().out == "/scratch/alice/build\n"
