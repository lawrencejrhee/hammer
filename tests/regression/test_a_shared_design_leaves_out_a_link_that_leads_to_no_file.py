import io
import os
import tarfile

import pytest

from hammer.vlsi import design_share

DEFS = "`define WIDTH 1\n"


@pytest.mark.parametrize("target", ["dangling", "link loop", "device"])
def test_a_link_in_a_bundled_dir_that_leads_to_no_file_is_left_out_and_the_design_shares_and_pulls(
        tmp_path, target) -> None:
    work = tmp_path / "alice" / "vlsi"
    (work / "build" / "Top").mkdir(parents=True)
    (work / "rtl" / "inc").mkdir(parents=True)
    (work / "rtl" / "inc" / "defs.vh").write_text(DEFS)
    link = work / "rtl" / "inc" / "mem_defs.vh"
    if target == "dangling":
        link.symlink_to(os.path.join("..", "..", "generated", "mem_defs.vh"))
    elif target == "link loop":
        link.symlink_to("mem_defs.vh")
    else:
        link.symlink_to(os.devnull)
    (work / "design.yml").write_text("synthesis.inputs.include_dirs: [rtl/inc]\n")

    bundle = design_share.build_bundle("Top", str(work), str(work / "build" / "Top"), [],
                                       {"default": [str(work / "design.yml")]}, "default", None,
                                       {"synthesis.inputs.include_dirs": ["rtl/inc"]}, "alice")
    assert sorted(bundle.manifest["files"]) == ["work/design.yml", "work/rtl/inc/defs.vh"]
    members = tarfile.open(fileobj=io.BytesIO(bundle.data), mode="r:gz").getmembers()
    assert all(m.isfile() for m in members)

    rec = {"manifest": bundle.manifest, "data": bundle.data, "bundle_sha": bundle.bundle_sha, "updated_at": None,
           "size_bytes": len(bundle.data)}
    into = tmp_path / "bob" / "Top-from-alice"
    design_share.pull("alice", "Top", str(into), fetch=lambda o, d: rec, build=False)
    assert (into / "rtl" / "inc" / "defs.vh").read_text() == DEFS
    assert not os.path.lexists(into / "rtl" / "inc" / "mem_defs.vh")
