import hashlib
import io
import os
import tarfile

import pytest

from hammer.shell import pd_store_cli
from hammer.vlsi import design_share, pd_store

FILES = {"work/rtl/top.v": os.urandom(64 * 1024), "work/design.yml": b"synthesis.inputs.top_module: Top\n"}


def _tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _rec(data: bytes, size=None) -> dict:
    manifest = {"version": design_share.BUNDLE_VERSION, "design": "Top", "source": {}, "obj_dir_rel": "build/Top",
                "rewrite": [], "files": {n: hashlib.sha256(b).hexdigest() for n, b in FILES.items()},
                "external": [], "hammer": {}, "env_configs": [], "proj_confs_by_tools": {"default": []},
                "default_tools": "default", "driver": None}
    return {"manifest": manifest, "data": data, "bundle_sha": "s", "updated_at": None,
            "size_bytes": len(data) if size is None else size}


GOOD = _tar(FILES)
FLIPPED = bytearray(GOOD)
FLIPPED[len(GOOD) // 2] ^= 0x10


@pytest.mark.parametrize("rec", [
    _rec(GOOD[:len(GOOD) // 2]),
    _rec(b""),
    _rec(b"not a bundle at all"),
    _rec(bytes(FLIPPED)),
    _rec(_tar(dict(FILES, **{"work/rtl/top.v": b"module Other;\n"}))),
    _rec(_tar(dict(FILES, **{"work/extra.v": b"module Extra;\n"}))),
    _rec(GOOD[:-100], size=len(GOOD)),
], ids=["truncated", "empty", "not gzip", "bit flipped", "file differs from manifest", "file not in manifest",
        "shorter than its stored size"])
def test_a_damaged_bundle_fails_the_pull_with_a_message_and_leaves_nothing_behind(tmp_path, monkeypatch, capsys,
                                                                                  rec) -> None:
    into = tmp_path / "Top-from-alice"
    monkeypatch.setattr(pd_store, "fetch_design_bundle", lambda o, d: rec)
    assert pd_store_cli.main(["pull", "alice", "Top", "--into", str(into), "--no-dag"]) == 2
    err = capsys.readouterr().err
    assert "studio pull: " in err and "damaged" in err and "Traceback" not in err
    assert not into.exists()

    monkeypatch.setattr(pd_store, "fetch_design_bundle", lambda o, d: _rec(GOOD))
    assert pd_store_cli.main(["pull", "alice", "Top", "--into", str(into), "--no-dag"]) == 0
    assert (into / "rtl" / "top.v").read_bytes() == FILES["work/rtl/top.v"]
