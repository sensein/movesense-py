"""Tests for data manifest and content-addressed storage."""

from pathlib import Path
import pytest
from movesense.server.manifest import DataManifest, content_hash


class TestContentHash:
    def test_hash_deterministic(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        h1 = content_hash(f)
        h2 = content_hash(f)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"hello")
        f2.write_bytes(b"world")
        assert content_hash(f1) != content_hash(f2)


class TestManifest:
    def test_empty_manifest(self, tmp_path):
        m = DataManifest(tmp_path)
        assert m.entries == []

    def test_register_and_dedup(self, tmp_path):
        m = DataManifest(tmp_path)
        f = tmp_path / "test.sbem"
        f.write_bytes(b"sensor data here")

        e1 = m.register(f, serial="S1", log_id=1)
        assert not e1["duplicate"]
        assert e1["content_hash"]

        e2 = m.register(f, serial="S1", log_id=1)
        assert e2["duplicate"]
        assert e2["content_hash"] == e1["content_hash"]

    def test_persist_and_reload(self, tmp_path):
        m = DataManifest(tmp_path)
        f = tmp_path / "test.sbem"
        f.write_bytes(b"data")
        m.register(f, serial="S1", log_id=1)

        m2 = DataManifest(tmp_path)
        assert len(m2.entries) == 1

    def test_time_ranges(self, tmp_path):
        m = DataManifest(tmp_path)
        f1 = tmp_path / "a.sbem"
        f2 = tmp_path / "b.sbem"
        f1.write_bytes(b"data1")
        f2.write_bytes(b"data2")

        m.register(f1, serial="S1", log_id=1, recording_start="2026-04-04T10:00:00Z", recording_end="2026-04-04T11:00:00Z")
        m.register(f2, serial="S1", log_id=2, recording_start="2026-04-05T10:00:00Z", recording_end="2026-04-05T11:00:00Z")

        ranges = m.get_time_ranges(serial="S1")
        assert len(ranges) == 2
        assert ranges[0]["recording_start"] < ranges[1]["recording_start"]

    def test_has_content(self, tmp_path):
        m = DataManifest(tmp_path)
        f = tmp_path / "test.sbem"
        f.write_bytes(b"data")
        h = content_hash(f)

        assert not m.has_content(h)
        m.register(f, serial="S1", log_id=1)
        assert m.has_content(h)

    def test_rebuild_from_disk(self, fake_data_dir):
        m = DataManifest(fake_data_dir)
        m.rebuild_from_disk()
        # fake_data_dir doesn't have .sbem files with content, but shouldn't crash
        assert isinstance(m.entries, list)
