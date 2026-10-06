"""`sledgehammer runs` shows the caller's own DAGs, matched by the _<user> suffix."""
from hammer.shell import sledgehammer_cli


def test_runs_matches_the_user_suffix_not_a_substring(monkeypatch, capsys) -> None:
    monkeypatch.setattr("getpass.getuser", lambda: "u")
    dags = "dag_id\nsledgehammer_Top_u\nsledgehammer_Top_uma\nsledgehammer_core_u_u\nother_u\n"
    asked = []

    def airflow(*a, capture=True):
        if a[:2] == ("dags", "list"):
            return 0, dags, ""
        asked.append(a[2])
        return 0, "dag_id run_id state\nx manual__1 success\n", ""

    monkeypatch.setattr(sledgehammer_cli, "_airflow", airflow)
    sledgehammer_cli._cmd_status([], list_only=True)
    assert asked == ["sledgehammer_Top_u", "sledgehammer_core_u_u"]
    out = capsys.readouterr().out.split("\n")
    assert [line.split()[0] for line in out if line.strip()] == ["Top", "core_u"]
