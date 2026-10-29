import hashlib
import io
import json
import tarfile

from hammer.vlsi import design_share


def _bundle(files: dict) -> dict:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    manifest = {"version": design_share.BUNDLE_VERSION, "design": "Top", "source": {}, "obj_dir_rel": "build/Top",
                "rewrite": [], "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
                "external": [], "hammer": {}, "env_configs": [], "proj_confs_by_tools": {"default": []},
                "default_tools": "default", "driver": None}
    return {"manifest": manifest, "data": buf.getvalue(), "bundle_sha": json.dumps(sorted(files)), "updated_at": None}


def test_a_repull_removes_dropped_files_but_never_a_path_its_state_file_names_outside_the_folder(tmp_path) -> None:
    into = tmp_path / "Top-from-alice"
    first = _bundle({"work/a.v": b"1\n", "work/old.v": b"dropped\n", "obj/gen.json": b"{}\n"})
    design_share.pull("alice", "Top", str(into), fetch=lambda o, d: first, build=False)
    assert (into / "old.v").is_file() and (into / "build" / "Top" / "gen.json").is_file()

    outside = tmp_path / "outside"
    outside.mkdir()
    victims = [tmp_path / "victim.txt", tmp_path / "victim2.txt", tmp_path / "victim3.txt", outside / "f.txt"]
    for v in victims:
        v.write_text("keep me\n")
    (into / "linked").symlink_to(outside, target_is_directory=True)
    state_path = into / design_share.STATE_FILE
    state = json.loads(state_path.read_text())
    state["files"] += [str(victims[0]), "../victim2.txt", str(into / "build" / "Top" / ".." / ".." / ".." /
                                                              "victim3.txt"), "linked/f.txt"]
    state_path.write_text(json.dumps(state))

    second = _bundle({"work/a.v": b"2\n"})
    design_share.pull("alice", "Top", str(into), fetch=lambda o, d: second, build=False)
    assert (into / "a.v").read_text() == "2\n"
    assert not (into / "old.v").exists() and not (into / "build" / "Top" / "gen.json").exists()
    assert all(v.read_text() == "keep me\n" for v in victims)
