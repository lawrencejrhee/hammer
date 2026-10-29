import hashlib
import io
import json
import os
import tarfile

import pytest

from hammer.vlsi import design_share, pd_store
from tests.utils.fake_bundle_db import FakeBundleDB

MIB = 1024 * 1024


def _bundle(files: dict) -> tuple:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    manifest = {"version": design_share.BUNDLE_VERSION, "design": "Top", "source": {}, "obj_dir_rel": "build/Top",
                "rewrite": [], "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
                "external": [], "hammer": {}, "env_configs": [], "proj_confs_by_tools": {"default": []},
                "default_tools": "default", "driver": None}
    return manifest, buf.getvalue(), hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("change", ["republished bigger", "republished small", "unshared"])
def test_a_pull_that_overlaps_a_republish_or_unshare_unpacks_the_bundle_it_started_reading(
        tmp_path, monkeypatch, change) -> None:
    db = FakeBundleDB().install(monkeypatch)
    first = {"work/rtl/top.v": os.urandom(9 * MIB), "work/design.yml": b"version: 1\n"}
    assert pd_store.publish_design_bundle("Top", *_bundle(first))
    assert len(db.chunks) == 2
    db.shares.add(("alice", "Top", "bob"))

    def owner_acts() -> None:
        db.user = "alice"
        if change == "unshared":
            db.shares.discard(("alice", "Top", "bob"))
        else:
            second = {"work/rtl/top.v": os.urandom(17 * MIB if change == "republished bigger" else 100),
                      "work/design.yml": b"version: 2\n", "work/rtl/new.v": b"module New;\n"}
            assert pd_store.publish_design_bundle("Top", *_bundle(second))
        db.user = "bob"

    db.after_bundle_read = owner_acts
    db.user = "bob"
    into = tmp_path / "Top-from-alice"
    design_share.pull("alice", "Top", str(into), build=False)
    assert db.after_bundle_read is None, "the owner acted between the pull's reads"
    assert (into / "rtl" / "top.v").read_bytes() == first["work/rtl/top.v"]
    assert (into / "design.yml").read_bytes() == b"version: 1\n" and not (into / "rtl" / "new.v").exists()
