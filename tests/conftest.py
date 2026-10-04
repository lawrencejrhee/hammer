import hashlib
import os

import pytest

from hammer.logging import HammerVLSILogging
from hammer.vlsi import rtl_check


@pytest.fixture(autouse=True)
def _restore_logging_callbacks():
    saved = list(HammerVLSILogging.callbacks)
    yield
    HammerVLSILogging.callbacks = saved


_PLACEHOLDER_RTL_TEST_MODULES = {"test_cli_driver", "test_flowgraph", "test_force_rerun", "test_rerun_messages"}


def _byte_hash_digest(paths, include_dirs=(), defines=(), top_module=None):
    file_hashes = []
    for path in sorted({os.path.realpath(p) for p in paths}):
        with open(path, "rb") as f:
            file_hashes.append(hashlib.sha256(f.read()).hexdigest() + "\n")
    return hashlib.sha256("".join(file_hashes).encode("utf-8")).hexdigest(), []


@pytest.fixture(autouse=True)
def _byte_hash_placeholder_rtl(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] in _PLACEHOLDER_RTL_TEST_MODULES:
        monkeypatch.setattr(rtl_check, "digest_units", _byte_hash_digest)
        monkeypatch.setattr(rtl_check, "digest_files", _byte_hash_digest)


@pytest.fixture(autouse=True)
def _no_developer_stack_env(monkeypatch):
    """Keep a developer's stack env file (~/.sledgehammer/env.sh, or a stack_env.sh above the cwd) out of the CLIs under test; only SLEDGE_ENV_FILE is honored."""
    from hammer.shell import sledgehammer_cli
    monkeypatch.delenv("SLEDGE_ENV_FILE", raising=False)
    monkeypatch.setattr(sledgehammer_cli, "_find_stack_env",
                        lambda: os.environ.get("SLEDGE_ENV_FILE") if os.path.isfile(os.environ.get("SLEDGE_ENV_FILE", "")) else None)
