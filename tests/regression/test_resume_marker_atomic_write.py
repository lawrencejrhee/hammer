import os

from hammer.vlsi import substep_resume
from hammer.vlsi.substep_resume import MARKER_NAME, read_marker, write_marker


def test_failed_write_leaves_the_previous_marker(tmp_path, monkeypatch) -> None:
    write_marker(str(tmp_path), {"stage_key": "A"})

    def dies_midway(obj, fh, **kw):
        fh.write('{"stage_key": "B", "bur')
        raise OSError("disk full")

    monkeypatch.setattr(substep_resume.json, "dump", dies_midway)
    write_marker(str(tmp_path), {"stage_key": "B"})
    assert read_marker(str(tmp_path)) == {"stage_key": "A"}
    assert os.listdir(tmp_path) == [MARKER_NAME]


def test_write_replaces_the_marker(tmp_path) -> None:
    write_marker(str(tmp_path), {"stage_key": "A"})
    write_marker(str(tmp_path), {"stage_key": "B"})
    assert read_marker(str(tmp_path)) == {"stage_key": "B"}
    assert os.listdir(tmp_path) == [MARKER_NAME]
