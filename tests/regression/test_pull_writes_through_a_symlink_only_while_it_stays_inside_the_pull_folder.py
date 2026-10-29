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
    return {"manifest": manifest, "data": buf.getvalue(), "bundle_sha": "x", "updated_at": None}


def _pull(how, into, rec, monkeypatch, capsys) -> None:
    if how == "pull":
        with pytest.raises(design_share.ShareError, match=r"is outside .*; refusing the bundle\.$"):
            design_share.pull("alice", "Top", str(into), fetch=lambda o, d: rec, force=True)
    else:
        monkeypatch.setattr(pd_store, "fetch_design_bundle", lambda o, d: rec)
        assert pd_store_cli.main(["pull", "alice", "Top", "--into", str(into), "--force", "--no-dag"]) == 2
        err = capsys.readouterr().err
        assert "is outside " in err and err.endswith("; refusing the bundle.\n")


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(design_share, "generate_dag", lambda *a, **k: pytest.fail("refused pulls build no DAG"))
    path = tmp_path / "elsewhere"
    path.mkdir()
    return path


@pytest.mark.parametrize("link, obj_dir_rel", [("buildlink", "buildlink"), ("build", "build/Top")])
@pytest.mark.parametrize("how", ["pull", "studio pull --no-dag"])
def test_a_build_dir_reached_through_a_symlink_out_of_the_folder_is_refused(tmp_path, monkeypatch, capsys,
                                                                            elsewhere, link, obj_dir_rel,
                                                                            how) -> None:
    into = tmp_path / "Top-from-alice"
    into.mkdir()
    (into / link).symlink_to(elsewhere, target_is_directory=True)
    _pull(how, into, _bundle(obj_dir_rel, {"obj/gen.json": b"{}\n", "work/top.v": b"module Top;\n"}),
          monkeypatch, capsys)
    assert os.listdir(elsewhere) == [] and sorted(os.listdir(into)) == [link]


@pytest.mark.parametrize("how", ["pull", "studio pull --no-dag"])
def test_a_file_under_a_symlink_out_of_the_folder_is_refused_before_anything_is_written(tmp_path, monkeypatch,
                                                                                        capsys, elsewhere,
                                                                                        how) -> None:
    into = tmp_path / "Top-from-alice"
    into.mkdir()
    (into / "rtl").symlink_to(elsewhere, target_is_directory=True)
    _pull(how, into, _bundle("build/Top", {"work/a.v": b"first\n", "work/rtl/top.v": b"module Top;\n"}),
          monkeypatch, capsys)
    assert os.listdir(elsewhere) == [] and not (into / "a.v").exists()


def test_a_symlink_whose_real_target_stays_inside_the_folder_is_written_and_cleaned_through(tmp_path,
                                                                                           elsewhere) -> None:
    into = tmp_path / "Top-from-alice"
    (into / "src").mkdir(parents=True)
    (into / "rtl").symlink_to(into / "src", target_is_directory=True)
    (into / "build").symlink_to("src", target_is_directory=True)
    first = _bundle("build/Top", {"work/rtl/top.v": b"1\n", "work/rtl/old.v": b"old\n", "obj/gen.json": b"{}\n"})
    design_share.pull("alice", "Top", str(into), fetch=lambda o, d: first, force=True, build=False)
    assert (into / "src" / "top.v").read_text() == "1\n" and (into / "src" / "old.v").is_file()
    assert (into / "src" / "Top" / "gen.json").is_file()

    second = _bundle("build/Top", {"work/rtl/top.v": b"2\n"})
    design_share.pull("alice", "Top", str(into), fetch=lambda o, d: second, build=False)
    assert (into / "src" / "top.v").read_text() == "2\n"
    assert not (into / "src" / "old.v").exists() and not (into / "src" / "Top" / "gen.json").exists()
    assert (into / "rtl").is_symlink() and (into / "build").is_symlink()

    (into / "src" / "ext").symlink_to(os.path.join("..", "..", "elsewhere"), target_is_directory=True)
    third = _bundle("build/Top", {"work/rtl/top.v": b"3\n", "work/rtl/ext/x.v": b"module X;\n"})
    with pytest.raises(design_share.ShareError, match=r"elsewhere.* is outside .*; refusing the bundle\.$"):
        design_share.pull("alice", "Top", str(into), fetch=lambda o, d: third, build=False)
    assert os.listdir(elsewhere) == [] and (into / "src" / "top.v").read_text() == "2\n"
