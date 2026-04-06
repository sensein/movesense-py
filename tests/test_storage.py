"""Tests for content-addressed blob store, provenance log, and device store."""

import json
from pathlib import Path

import pytest

from movesense.storage import (
    BlobStore,
    DeviceStore,
    ProvLog,
    content_hash,
    device_ts_to_utc,
    normalize_timestamp,
)


@pytest.fixture
def device_dir(tmp_path):
    d = tmp_path / "254830002158"
    d.mkdir()
    return d


@pytest.fixture
def sample_sbem(tmp_path):
    """Create a fake SBEM file with known content."""
    f = tmp_path / "test_log.sbem"
    f.write_bytes(b"SBEM_FAKE_CONTENT_12345")
    return f


class TestContentHash:
    def test_consistent_hash(self, sample_sbem):
        h1 = content_hash(sample_sbem)
        h2 = content_hash(sample_sbem)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content_a")
        f2.write_bytes(b"content_b")
        assert content_hash(f1) != content_hash(f2)


class TestTimestampNormalization:
    def test_ms_to_us(self):
        assert normalize_timestamp(1000, "ms") == 1_000_000

    def test_us_identity(self):
        assert normalize_timestamp(1_000_000, "us") == 1_000_000

    def test_utc_conversion(self):
        mapping = {"relative_time_us": 122_000_000, "utc_time_us": 1_712_240_400_000_000}
        ts_us = 122_500_000  # 500ms after anchor
        utc = device_ts_to_utc(ts_us, mapping)
        assert utc == 1_712_240_400_500_000

    def test_utc_round_trip(self):
        mapping = {"relative_time_us": 100_000, "utc_time_us": 1_700_000_000_000_000}
        ts_us = 200_000
        utc = device_ts_to_utc(ts_us, mapping)
        # Reverse: ts_us = utc - utc_time_us + relative_time_us
        recovered = utc - mapping["utc_time_us"] + mapping["relative_time_us"]
        assert recovered == ts_us


class TestBlobStore:
    def test_store_new_file(self, device_dir, sample_sbem):
        store = BlobStore(device_dir)
        h = store.store(sample_sbem)
        assert store.exists(h)
        assert store.path(h).exists()
        assert store.path(h).read_bytes() == sample_sbem.read_bytes()

    def test_detect_duplicate(self, device_dir, sample_sbem):
        store = BlobStore(device_dir)
        h1 = store.store(sample_sbem)
        h2 = store.store(sample_sbem)
        assert h1 == h2

    def test_hash_prefix_directory(self, device_dir, sample_sbem):
        store = BlobStore(device_dir)
        h = store.store(sample_sbem)
        blob_path = store.path(h)
        assert blob_path.parent.name == h[:2]

    def test_rebuild_index(self, device_dir, sample_sbem):
        store = BlobStore(device_dir)
        h = store.store(sample_sbem)
        hashes = store.rebuild_index()
        assert h in hashes

    def test_exists_false_for_unknown(self, device_dir):
        store = BlobStore(device_dir)
        assert not store.exists("0000000000000000000000000000000000000000000000000000000000000000")


class TestProvLog:
    def test_record_and_find(self, device_dir):
        prov = ProvLog(device_dir)
        entry = prov.record("abc123", "log_1.sbem", "254830002158", 1, 0, ["ECG", "IMU9"])
        assert entry["hash"] == "abc123"
        found = prov.find_by_hash("abc123")
        assert found is not None
        assert found["session_index"] == 0

    def test_has_hash(self, device_dir):
        prov = ProvLog(device_dir)
        assert not prov.has_hash("xyz")
        prov.record("xyz", "log.sbem", "serial", 1, 0, [])
        assert prov.has_hash("xyz")

    def test_missing_file(self, device_dir):
        prov = ProvLog(device_dir)
        assert prov.find_by_hash("nonexistent") is None

    def test_multiple_records(self, device_dir):
        prov = ProvLog(device_dir)
        prov.record("h1", "log_1.sbem", "s", 1, 0, ["ECG"])
        prov.record("h2", "log_2.sbem", "s", 2, 1, ["IMU9"])
        assert prov.has_hash("h1")
        assert prov.has_hash("h2")
        lines = prov.log_file.read_text().strip().split("\n")
        assert len(lines) == 2


class TestDeviceStore:
    def test_create_new_store(self, device_dir):
        ds = DeviceStore(device_dir)
        ds.open()
        assert ds.next_session_index() == 0
        assert ds.get_sessions_index() == {}

    def test_add_session(self, device_dir):
        ds = DeviceStore(device_dir)
        ds.open()
        group = ds.add_session(0, {"device_serial": "254830002158", "firmware_version": "1.0.1"})
        assert group is not None
        assert ds.root["0"].attrs["device_serial"] == "254830002158"

    def test_update_sessions_index(self, device_dir):
        ds = DeviceStore(device_dir)
        ds.open()
        ds.add_session(0)
        ds.update_sessions_index(0, {
            "start_utc": "2026-04-04T14:00:00.000000Z",
            "start_utc_us": 1_712_240_400_000_000,
            "end_utc": "2026-04-04T14:42:15.123456Z",
            "end_utc_us": 1_712_242_935_123_456,
            "duration_seconds": 2535.123,
            "channels": {
                "MeasECGmV": {"rate_hz": 200, "samples": 507000},
                "MeasIMU9": {"rate_hz": 52, "samples": 131820},
            },
        })
        idx = ds.get_sessions_index()
        assert "0" in idx
        assert idx["0"]["start_utc_us"] == 1_712_240_400_000_000
        assert "MeasECGmV" in idx["0"]["channels"]

    def test_multiple_sessions(self, device_dir):
        ds = DeviceStore(device_dir)
        ds.open()
        ds.add_session(0)
        ds.update_sessions_index(0, {"start_utc": "2026-04-04T14:00:00.000000Z", "channels": {}})
        ds.add_session(1)
        ds.update_sessions_index(1, {"start_utc": "2026-04-05T09:00:00.000000Z", "channels": {}})
        assert ds.next_session_index() == 2

    def test_stream_group(self, device_dir):
        ds = DeviceStore(device_dir)
        ds.open()
        group, idx = ds.open_stream_session()
        assert idx == 0
        assert ds.root["stream"].attrs["trust_level"] == "low"
        group2, idx2 = ds.open_stream_session()
        assert idx2 == 1

    def test_different_channels_per_session(self, device_dir):
        ds = DeviceStore(device_dir)
        ds.open()
        ds.add_session(0)
        ds.update_sessions_index(0, {
            "start_utc": "2026-04-04T14:00:00.000000Z",
            "channels": {"MeasECGmV": {"rate_hz": 200}, "MeasIMU9": {"rate_hz": 52}},
        })
        ds.add_session(1)
        ds.update_sessions_index(1, {
            "start_utc": "2026-04-05T09:00:00.000000Z",
            "channels": {"MeasECGmV": {"rate_hz": 500}, "MeasHR": {"rate_hz": 1}},
        })
        idx = ds.get_sessions_index()
        assert "MeasIMU9" in idx["0"]["channels"]
        assert "MeasIMU9" not in idx["1"]["channels"]
        assert idx["1"]["channels"]["MeasECGmV"]["rate_hz"] == 500
