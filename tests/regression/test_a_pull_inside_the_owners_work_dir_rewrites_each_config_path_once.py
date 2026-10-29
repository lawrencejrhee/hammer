"""Pulling a design into a directory inside the owner's working directory points each of the owner's paths in
the configs at the pulled copy once."""
import io
import tarfile

from hammer.vlsi import design_share


def _tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def test_a_build_dir_outside_the_work_dir_is_not_rewritten_twice(tmp_path):
    owner_work = tmp_path / "alice" / "project"
    owner_obj = tmp_path / "scratch" / "alice" / "build" / "top"
    config = (f"x: {owner_obj}/in.yml\n"
              f"synthesis.inputs.input_files: [\"{owner_work}/rtl/top.v\"]\n").encode()
    manifest = {"source": {"work_dir": str(owner_work), "obj_dir": str(owner_obj)}, "obj_dir_rel": None,
                "rewrite": ["work/design.yml"]}
    work = owner_work / "build-from-alice"
    obj = work / "build" / "top"

    design_share.unpack(_tar({"work/design.yml": config}), manifest, str(work), str(obj))

    assert (work / "design.yml").read_text() == (f"x: {obj}/in.yml\n"
                                                 f"synthesis.inputs.input_files: [\"{work}/rtl/top.v\"]\n")
