#  SledgeHammer test configuration

import hashlib
import os

import pytest

from hammer.vlsi import rtl_check

# The upstream CLI-driver and flowgraph tests give placeholder files (/dev/null, LICENSE,
# README.md) as synthesis inputs. The pre-slang RTL fingerprint byte-hashed them; slang
# rejects them, which failed these 14 tests. They exercise the driver, not the
# fingerprint (tests/test_rtl_check.py covers that), so they get the pre-slang byte hash,
# which still changes when an input changes, and the upstream files stay unmodified.
_PLACEHOLDER_RTL_TEST_MODULES = {"test_cli_driver", "test_flowgraph"}


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
