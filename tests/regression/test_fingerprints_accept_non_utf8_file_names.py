import hashlib
import os
import sys

import pytest

from hammer.vlsi import fingerprints as fp

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs a file system that takes any bytes")


def _bytes_file(directory, name: bytes, data: bytes = b"x\n") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with open(os.path.join(os.fsencode(str(directory)), name), "wb") as f:
        f.write(data)


def test_a_deck_dir_with_a_latin1_name_still_gives_a_key(tmp_path):
    deck_dir = tmp_path / "pegasus"
    _bytes_file(deck_dir, b"r\xe9vision_notes.txt")
    deck = deck_dir / "drc.rul"
    deck.write_text("rule 1\n")
    lines = fp.sibling_lines(str(deck))
    assert any("\udce9" in line for line in lines)
    key = fp.digest_lines(lines)
    _bytes_file(deck_dir, b"r\xe9vision_notes.txt", b"longer\n")
    assert fp.digest_lines(fp.sibling_lines(str(deck))) != key


def test_a_latin1_header_in_an_include_dir_still_gives_a_key(tmp_path):
    inc = tmp_path / "inc"
    _bytes_file(inc, b"caf\xe9.vh", b"`define W 4\n")
    lines = fp.include_dir_lines([str(inc)])
    assert len(lines) == 1
    assert len(fp.digest_lines(lines)) == 64


def test_valid_utf8_lines_keep_their_digest():
    lines = ["/pdk/café.lef:1:2:3", "/pdk/a.lef:1:2:3"]
    expected = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
    assert fp.digest_lines(lines) == expected


def test_the_debug_file_records_a_latin1_name_as_its_bytes(tmp_path, monkeypatch):
    out = tmp_path / "collat.txt"
    monkeypatch.setenv(fp.DEBUG_ENV, str(out))
    fp.debug_record("drc.collateral", ["/pdk/r\udce9vision_notes.txt:1:2:3"])
    assert out.read_bytes() == b"drc.collateral\t/pdk/r\xe9vision_notes.txt:1:2:3\n"
