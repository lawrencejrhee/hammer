import os
import tracemalloc

from hammer.vlsi import pd_store
from tests.utils.fake_bundle_db import FakeBundleDB

MIB = 1024 * 1024


def test_a_big_bundle_goes_up_in_8_mib_chunks_and_comes_back_whole_one_chunk_at_a_time(monkeypatch) -> None:
    db = FakeBundleDB().install(monkeypatch)
    data = os.urandom(20 * MIB + 5)
    assert pd_store.publish_design_bundle("Top", {"design": "Top"}, data, "a" * 64)
    row = db.bundles[("alice", "Top")]
    assert len(row["data"]) == 0 and row["size_bytes"] == len(data)
    assert [len(db.chunks[k]) for k in sorted(db.chunks)] == [8 * MIB, 8 * MIB, 4 * MIB + 5]
    rec = pd_store.fetch_design_bundle("alice", "Top")
    assert rec["data"] == data and rec["size_bytes"] == len(data) and rec["manifest"] == {"design": "Top"}
    assert db.named == ["pd_chunk_reader"]


def test_a_small_bundle_stays_inline(monkeypatch) -> None:
    db = FakeBundleDB().install(monkeypatch)
    data = os.urandom(3 * MIB)
    assert pd_store.publish_design_bundle("Top", {}, data, "b" * 64)
    assert bytes(db.bundles[("alice", "Top")]["data"]) == data and not db.chunks
    assert pd_store.fetch_design_bundle("alice", "Top")["data"] == data and db.named == []


def test_publishing_a_big_bundle_never_copies_it_and_an_unchanged_one_uploads_nothing(monkeypatch) -> None:
    db = FakeBundleDB().install(monkeypatch)
    data = os.urandom(64 * MIB)
    tracemalloc.start()
    try:
        assert pd_store.publish_design_bundle("Top", {}, data, "c" * 64)
        changed = tracemalloc.get_traced_memory()[1]
        tracemalloc.reset_peak()
        before = len(db.statements)
        assert not pd_store.publish_design_bundle("Top", {}, data, "c" * 64)
        unchanged = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert changed < 4 * MIB and unchanged < 4 * MIB
    assert [s for s, _ in db.statements[before:]] == [
        f"SELECT bundle_sha FROM {pd_store.FQ_DESIGN_BUNDLE} WHERE owner = current_user AND design = %s"]
