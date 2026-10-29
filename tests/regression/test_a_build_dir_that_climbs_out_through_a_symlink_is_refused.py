import hashlib
import io
import os
import tarfile

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
    return {"manifest": manifest, "data": buf.getvalue(), "bundle_sha": obj_dir_rel, "updated_at": None}


@pytest.mark.parametrize("obj_dir_rel", ["scratch/../.ssh", "scratch/./../.ssh", "build/Top/../../scratch/../.ssh",
                                         "a/scr/../../.ssh"])
@pytest.mark.parametrize("repull", [True, False], ids=["re-pull", "first pull with --force"])
@pytest.mark.parametrize("how", ["pull", "studio pull --no-dag"])
def test_a_build_dir_that_climbs_back_out_through_a_symlink_in_the_pull_folder_writes_nothing_outside_it(
        tmp_path, monkeypatch, capsys, obj_dir_rel, repull, how) -> None:
    home = tmp_path / "home" / "bob"
    (home / "scratch" / "deep").mkdir(parents=True)
    (home / ".ssh").mkdir()
    keys = home / ".ssh" / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAA bob\n")
    into = home / "Top-from-alice"
    if repull:
        first = _bundle("build/Top", {"obj/gen.json": b"{}\n", "work/top.v": b"module Top;\n"})
        design_share.pull("alice", "Top", str(into), fetch=lambda o, d: first, build=False)
    (into / "a").mkdir(parents=True)
    (into / "scratch").symlink_to(home / "scratch", target_is_directory=True)
    (into / "a" / "scr").symlink_to(home / "scratch" / "deep", target_is_directory=True)
    before = sorted(os.listdir(into))
    force = [] if repull else ["--force"]
    rec = _bundle(obj_dir_rel, {"obj/authorized_keys": b"ssh-ed25519 AAAA alice\n", "work/top.v": b"module Top;\n"})
    if how == "pull":
        with pytest.raises(design_share.ShareError, match=r"is outside .*; refusing the bundle\.$"):
            design_share.pull("alice", "Top", str(into), fetch=lambda o, d: rec, force=bool(force), build=False)
    else:
        monkeypatch.setattr(pd_store, "fetch_design_bundle", lambda o, d: rec)
        assert pd_store_cli.main(["pull", "alice", "Top", "--into", str(into), "--no-dag"] + force) == 2
        assert capsys.readouterr().err.endswith("; refusing the bundle.\n")
    assert keys.read_text() == "ssh-ed25519 AAAA bob\n" and os.listdir(home / ".ssh") == ["authorized_keys"]
    assert os.listdir(home / "scratch") == ["deep"] and os.listdir(home / "scratch" / "deep") == []
    assert sorted(os.listdir(into)) == before
