import hashlib
import io
import json
import tarfile

import pytest

from hammer.shell import pd_store_cli
from hammer.vlsi import design_share, pd_store

FILES = {"work/rtl/top.v": b"module Top; endmodule\n", "obj/gen.sdc": b"set_max_fanout 8 [current_design]\n",
         "obj/sram_generator-output.json": b"{}\n"}


def _bundle(files: dict) -> dict:
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
    return {"manifest": manifest, "data": buf.getvalue(), "bundle_sha": repr(sorted(files)), "updated_at": None}


def _home_and_users(tmp_path) -> dict:
    (tmp_path / "users" / "bob").mkdir(parents=True)
    (tmp_path / "home").symlink_to("users", target_is_directory=True)
    return {top: tmp_path / top / "bob" / "Top-from-alice" for top in ("home", "users")}


@pytest.mark.parametrize("first, second", [("home", "users"), ("users", "home"), ("home", ".")],
                         ids=["symlinked then real", "real then symlinked", "symlinked then --into . inside it"])
@pytest.mark.parametrize("how", ["pull", "studio pull --no-dag"])
def test_a_repull_that_reaches_the_folder_another_way_keeps_the_files_it_writes_and_drops_the_rest(
        tmp_path, monkeypatch, first, second, how) -> None:
    into = _home_and_users(tmp_path)
    folder = into["users"]

    def pull(spelling, rec) -> None:
        if spelling == ".":
            monkeypatch.chdir(into["home"])
        target = spelling if spelling == "." else str(into[spelling])
        if how == "pull":
            design_share.pull("alice", "Top", target, fetch=lambda o, d: rec, build=False)
        else:
            monkeypatch.setattr(pd_store, "fetch_design_bundle", lambda o, d: rec)
            assert pd_store_cli.main(["pull", "alice", "Top", "--into", target, "--no-dag"]) == 0

    pull(first, _bundle(FILES))
    pull(second, _bundle(FILES))
    assert (folder / "rtl" / "top.v").read_bytes() == FILES["work/rtl/top.v"]
    for name in ("gen.sdc", "sram_generator-output.json"):
        assert (folder / "build" / "Top" / name).read_bytes() == FILES[f"obj/{name}"]

    pull(first, _bundle({n: b for n, b in FILES.items() if n != "obj/gen.sdc"}))
    assert not (folder / "build" / "Top" / "gen.sdc").exists()
    assert (folder / "build" / "Top" / "sram_generator-output.json").read_bytes() == b"{}\n"
    assert (folder / "rtl" / "top.v").read_bytes() == FILES["work/rtl/top.v"]


def test_a_repull_keeps_the_build_dir_files_its_state_file_names_through_a_symlinked_path(tmp_path) -> None:
    into = _home_and_users(tmp_path)
    folder = into["users"]
    rec = _bundle(FILES)
    design_share.pull("alice", "Top", str(folder), fetch=lambda o, d: rec, build=False)
    state_path = folder / design_share.STATE_FILE
    for via in ("home", "users"):
        state = json.loads(state_path.read_text())
        state["files"] = [f.replace(str(into["users"]), str(into["home"]), 1) for f in state["files"]]
        state_path.write_text(json.dumps(state))
        design_share.pull("alice", "Top", str(into[via]), fetch=lambda o, d: rec, build=False)
        for name in ("gen.sdc", "sram_generator-output.json"):
            assert (folder / "build" / "Top" / name).read_bytes() == FILES[f"obj/{name}"], via
