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
