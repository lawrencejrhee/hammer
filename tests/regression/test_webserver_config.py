import logging
import os
import runpy
import sys
import types

import pytest

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "webserver_config.py")
LDAP_NAMES = ("ldap", "ldap.dn", "ldap.filter")


@pytest.fixture
def load_config(monkeypatch):
    """Run webserver_config.py with FAB, the Airflow override and auth2fa stubbed; no Airflow install needed."""
    manager = types.ModuleType("flask_appbuilder.security.manager")
    manager.AUTH_LDAP = 2
    override = types.ModuleType("airflow.providers.fab.auth_manager.security_manager.override")

    class FabAirflowSecurityManagerOverride:
        def auth_user_ldap(self, username, password, rotate_session_id=True):
            return None

        def _ldap_bind_indirect(self, ldap, con):
            pass

    override.FabAirflowSecurityManagerOverride = FabAirflowSecurityManagerOverride
    fab_integration = types.ModuleType("auth2fa.fab_integration")
    fab_integration.install_2fa = lambda: False
    monkeypatch.setitem(sys.modules, manager.__name__, manager)
    monkeypatch.setitem(sys.modules, override.__name__, override)
    monkeypatch.setitem(sys.modules, fab_integration.__name__, fab_integration)
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in LDAP_NAMES:
        monkeypatch.setitem(sys.modules, name, None)
        monkeypatch.delitem(sys.modules, name)
    return lambda: runpy.run_path(CONFIG)


def test_loads_without_python_ldap(load_config, monkeypatch, caplog) -> None:
    monkeypatch.setitem(sys.modules, "ldap", None)
    with caplog.at_level(logging.ERROR, logger="airflow.webserver_config"):
        config = load_config()
    assert config["AUTH_TYPE"] == 2
    assert any("python-ldap is unavailable" in r.getMessage() for r in caplog.records)


def test_imports_ldap_submodules_when_present(load_config, tmp_path, monkeypatch, caplog) -> None:
    pkg = tmp_path / "ldap"
    pkg.mkdir()
    for name in ("__init__", "dn", "filter"):
        (pkg / f"{name}.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    with caplog.at_level(logging.ERROR, logger="airflow.webserver_config"):
        load_config()
    assert all(sys.modules.get(n) is not None for n in LDAP_NAMES)
    assert not any("python-ldap" in r.getMessage() for r in caplog.records)
