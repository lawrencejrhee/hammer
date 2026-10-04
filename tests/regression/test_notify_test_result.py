import smtplib

import pytest

from hammer.shell import pd_store_cli
from hammer.vlsi import pd_notify


@pytest.fixture
def smtp(monkeypatch, tmp_path):
    """SLEDGE_SMTP_* set to a fake sender; returns the list of messages the fake server accepted."""
    pw = tmp_path / "pw"
    pw.write_text("secret\n")
    monkeypatch.setattr(pd_store_cli, "_load_smtp_env", lambda: None)
    monkeypatch.setenv("SLEDGE_SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SLEDGE_SMTP_PASSWORD_FILE", str(pw))
    sent = []

    class Server:
        def __init__(self, host, port, timeout=None):
            pass

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            sent.append(msg)

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", Server)
    return sent


def test_reports_sent_only_when_the_server_accepted(smtp, capsys) -> None:
    assert pd_store_cli.main(["notify-test", "--to", "me@example.com"]) == 0
    assert len(smtp) == 1
    assert "test email sent to me@example.com" in capsys.readouterr().out


def test_smtp_failure_exits_nonzero(smtp, monkeypatch, capsys) -> None:
    def refuse(*a, **k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    assert pd_store_cli.main(["notify-test", "--to", "me@example.com"]) == 1
    out, err = capsys.readouterr()
    assert "connection refused" in err and "sent" not in out


def test_no_password_file_is_not_configured(smtp, monkeypatch, capsys) -> None:
    monkeypatch.delenv("SLEDGE_SMTP_PASSWORD_FILE")
    assert pd_store_cli.main(["notify-test", "--to", "me@example.com"]) == 2
    assert smtp == []
    assert "smtp-setup" in capsys.readouterr().err


def test_sender_says_whether_it_sent(smtp, monkeypatch) -> None:
    assert pd_notify._send_completion_email("me@example.com", "s", "<p>x</p>") is True
    monkeypatch.delenv("SLEDGE_SMTP_USER")
    assert pd_notify._send_completion_email("me@example.com", "s", "<p>x</p>") is False


def test_a_starttls_failure_is_reported_not_the_quit_after_it(smtp, monkeypatch, capsys) -> None:
    class Refuses:
        def __init__(self, *a, **k):
            pass

        def ehlo(self):
            pass

        def starttls(self, context=None):
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")

        def quit(self):
            raise smtplib.SMTPServerDisconnected("please run connect() first")

    monkeypatch.setattr(smtplib, "SMTP", Refuses)
    assert pd_store_cli.main(["notify-test", "--to", "me@example.com"]) == 1
    assert "STARTTLS extension not supported" in capsys.readouterr().err
