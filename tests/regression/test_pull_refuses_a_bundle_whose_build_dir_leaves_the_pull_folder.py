import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest

from hammer.shell import pd_store_cli
from hammer.vlsi import design_share, pd_store


def _bundle(obj_dir_rel, files: dict) -> dict:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    manifest = {"version": design_share.BUNDLE_VERSION, "design": "Top", "source": {}, "obj_dir_rel": obj_dir_rel,
                "rewrite": [], "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
                "external": [], "hammer": {}, "env_configs": [], "proj_confs_by_tools": {"default": []},
                "default_tools": "default", "driver": None}
    return {"manifest": manifest, "data": buf.getvalue(), "bundle_sha": "x", "updated_at": None}


def _tree(root) -> set:
    return {os.path.join(top, n) for top, dirs, files in os.walk(root) for n in dirs + files}


@pytest.mark.parametrize("obj_dir_rel", ["../escaped", "../../escaped", "build/../../escaped", "/abs/escaped"])
@pytest.mark.parametrize("how", ["pull", "studio pull --no-dag"])
def test_a_bundle_whose_build_dir_leaves_the_pull_folder_is_refused_and_writes_nothing(
        tmp_path, monkeypatch, capsys, obj_dir_rel, how) -> None:
    into = tmp_path / "a" / "b" / "Top-from-alice"
    (tmp_path / "a" / "b").mkdir(parents=True)
    victim = None if os.path.isabs(obj_dir_rel) else Path(os.path.normpath(into / obj_dir_rel / "authorized_keys"))
    if victim is not None:
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_text("ssh-ed25519 AAAA bob\n")
    before = _tree(tmp_path)
    rec = _bundle(obj_dir_rel, {"obj/authorized_keys": b"ssh-ed25519 AAAA alice\n", "work/top.v": b"module Top;\n"})
    dags = []
    monkeypatch.setattr(design_share, "generate_dag", lambda *a, **k: dags.append(a) or "dag")
    if how == "pull":
        with pytest.raises(design_share.ShareError, match="refusing"):
            design_share.pull("alice", "Top", str(into), fetch=lambda o, d: rec)
    else:
        monkeypatch.setattr(pd_store, "fetch_design_bundle", lambda o, d: rec)
        assert pd_store_cli.main(["pull", "alice", "Top", "--into", str(into), "--no-dag"]) == 2
        assert "refusing" in capsys.readouterr().err
    assert _tree(tmp_path) == before and not dags
    assert victim is None or victim.read_text() == "ssh-ed25519 AAAA bob\n"
