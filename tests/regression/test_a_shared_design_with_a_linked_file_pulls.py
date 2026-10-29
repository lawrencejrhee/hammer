import io
import os
import tarfile

import pytest

from hammer.vlsi import design_share

TOP = "module Top; endmodule\n"
CONFIG = "synthesis.inputs.input_files: [rtl/top.v, src/top.v]\n"


def _share(work) -> design_share.Bundle:
    return design_share.build_bundle("Top", str(work), str(work / "build" / "Top"), [],
                                     {"default": [str(work / "design.yml")]}, "default", None,
                                     {"synthesis.inputs.input_files": ["rtl/top.v", "src/top.v"]}, "alice")


@pytest.mark.parametrize("link", ["symlink", "hard link", "symlink out of the checkout", "symlinked config"])
def test_a_design_whose_files_include_a_symlink_or_a_hard_link_pulls_with_their_contents(tmp_path, link) -> None:
    work = tmp_path / "alice" / "vlsi"
    for d in (work / "build" / "Top", work / "src", work / "rtl", work / "cfg", tmp_path / "shared"):
        d.mkdir(parents=True)
    (work / "src" / "top.v").write_text(TOP)
    (tmp_path / "shared" / "top.v").write_text(TOP)
    (work / "cfg" / "real.yml").write_text(CONFIG)
    linked = work / ("design.yml" if link == "symlinked config" else "rtl/top.v")
    if link == "symlink":
        linked.symlink_to(os.path.join("..", "src", "top.v"))
    elif link == "hard link":
        os.link(work / "src" / "top.v", linked)
    elif link == "symlink out of the checkout":
        linked.symlink_to(tmp_path / "shared" / "top.v")
    else:
        linked.symlink_to(os.path.join("cfg", "real.yml"))
    for plain, body in ((work / "rtl" / "top.v", TOP), (work / "design.yml", CONFIG)):
        if plain != linked:
            plain.write_text(body)

    bundle = _share(work)
    rec = {"manifest": bundle.manifest, "data": bundle.data, "bundle_sha": bundle.bundle_sha, "updated_at": None,
           "size_bytes": len(bundle.data)}
    into = tmp_path / "bob" / "Top-from-alice"
    design_share.pull("alice", "Top", str(into), fetch=lambda o, d: rec, build=False)
    for name, body in (("rtl/top.v", TOP), ("src/top.v", TOP), ("design.yml", CONFIG)):
        path = into / name
        assert path.is_file() and not path.is_symlink() and path.read_text() == body
    members = tarfile.open(fileobj=io.BytesIO(bundle.data), mode="r:gz").getmembers()
    assert sorted(m.name for m in members) == ["work/design.yml", "work/rtl/top.v", "work/src/top.v"]
    assert all(m.isfile() for m in members)

    body = linked.read_bytes()
    linked.unlink()
    linked.write_bytes(body)
    assert _share(work).bundle_sha == bundle.bundle_sha
