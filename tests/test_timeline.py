"""Tests for timeline API: cross-session queries with gap markers."""

import json
from pathlib import Path

import numpy as np
import pytest
import zarr

from movesense.storage import DeviceStore


@pytest.fixture
def device_store(tmp_path):
    """Create a DeviceStore with two sessions and a gap between them."""
    device_dir = tmp_path / "000000000000"
    device_dir.mkdir()
    ds = DeviceStore(device_dir)
    ds.open()

    # Session 0: ECG + Temp, starts at UTC 1712240400000000 (2024-04-04T14:00:00Z)
    g0 = ds.add_session(0, {
        "device_serial": "000000000000",
        "timestamp_mapping": {
            "relative_time_us": 100_000_000,
            "utc_time_us": 1_712_240_400_000_000,
        },
    })
    # ECG: 200Hz, 2000 samples = 10 seconds
    ecg0 = g0.require_group("MeasEcgmV")
    ecg_data = np.sin(np.linspace(0, 20 * np.pi, 2000)).astype(np.float32)
    ecg0.create_array("data", data=ecg_data)
    ecg0.attrs["sampling_rate_hz"] = 200.0
    ecg0.attrs["sensor_type"] = "ECG"
    ecg0.attrs["unit"] = "mV"
    ecg_ts = (np.arange(2000) * 5000 + 100_000_000).astype(np.uint64)  # µs, 5ms apart
    ecg0.create_array("timestamps", data=ecg_ts)

    # Temp: 1Hz, 10 samples
    temp0 = g0.require_group("MeasTemp")
    temp0.create_array("data", data=np.array([306.8] * 10, dtype=np.float32))
    temp0.attrs["sampling_rate_hz"] = 1.0
    temp0.attrs["sensor_type"] = "Temperature"
    temp0.attrs["unit"] = "K"

    ds.update_sessions_index(0, {
        "start_utc": "2024-04-04T14:00:00.000000Z",
        "start_utc_us": 1_712_240_400_000_000,
        "end_utc": "2024-04-04T14:00:10.000000Z",
        "end_utc_us": 1_712_240_410_000_000,
        "duration_seconds": 10.0,
        "channels": {
            "MeasEcgmV": {"rate_hz": 200, "samples": 2000},
            "MeasTemp": {"rate_hz": 1, "samples": 10},
        },
    })

    # Session 1: ECG only (different rate), starts 5 min later
    # UTC 1712240700000000 (2024-04-04T14:05:00Z) — 5 min gap
    g1 = ds.add_session(1, {
        "device_serial": "000000000000",
        "timestamp_mapping": {
            "relative_time_us": 400_000_000,
            "utc_time_us": 1_712_240_700_000_000,
        },
    })
    ecg1 = g1.require_group("MeasEcgmV")
    ecg_data1 = np.cos(np.linspace(0, 10 * np.pi, 1000)).astype(np.float32)
    ecg1.create_array("data", data=ecg_data1)
    ecg1.attrs["sampling_rate_hz"] = 500.0
    ecg1.attrs["sensor_type"] = "ECG"
    ecg1.attrs["unit"] = "mV"

    ds.update_sessions_index(1, {
        "start_utc": "2024-04-04T14:05:00.000000Z",
        "start_utc_us": 1_712_240_700_000_000,
        "end_utc": "2024-04-04T14:05:02.000000Z",
        "end_utc_us": 1_712_240_702_000_000,
        "duration_seconds": 2.0,
        "channels": {
            "MeasEcgmV": {"rate_hz": 500, "samples": 1000},
        },
    })

    ds.close()
    return device_dir


def _query_timeline(device_dir, serial, start_utc_us, end_utc_us, channel=None, buckets=0, target_rate=None):
    """Helper: call timeline query logic directly."""
    from movesense.server.timeline import query_timeline
    return query_timeline(
        device_dir.parent, serial,
        start_utc_us=start_utc_us,
        end_utc_us=end_utc_us,
        channel=channel,
        buckets=buckets,
        target_rate=target_rate,
    )


class TestTimelineCrossSessions:
    def test_two_sessions_with_gap(self, device_store):
        """Query spanning both sessions should return 2 segments + 1 gap."""
        result = _query_timeline(
            device_store, "000000000000",
            start_utc_us=1_712_240_400_000_000,
            end_utc_us=1_712_240_710_000_000,
            channel="MeasEcgmV",
        )
        segments = result["segments"]
        # Should have: segment(session 0), gap, segment(session 1)
        data_segments = [s for s in segments if s.get("type") != "gap"]
        gaps = [s for s in segments if s.get("type") == "gap"]
        assert len(data_segments) == 2
        assert len(gaps) == 1
        assert gaps[0]["duration_seconds"] == pytest.approx(290.0, abs=1)  # ~5 min gap

    def test_single_session_no_gap(self, device_store):
        """Query within one session should return 1 segment, no gaps."""
        result = _query_timeline(
            device_store, "000000000000",
            start_utc_us=1_712_240_400_000_000,
            end_utc_us=1_712_240_410_000_000,
            channel="MeasEcgmV",
        )
        segments = result["segments"]
        data_segments = [s for s in segments if s.get("type") != "gap"]
        gaps = [s for s in segments if s.get("type") == "gap"]
        assert len(data_segments) == 1
        assert len(gaps) == 0
        assert data_segments[0]["session_index"] == 0

    def test_utc_precision_in_response(self, device_store):
        """UTC timestamps in response should have µs precision."""
        result = _query_timeline(
            device_store, "000000000000",
            start_utc_us=1_712_240_400_000_000,
            end_utc_us=1_712_240_410_000_000,
            channel="MeasEcgmV",
        )
        seg = result["segments"][0]
        assert "start_utc_us" in seg
        assert "start_utc" in seg
        assert ".000000Z" in seg["start_utc"]


class TestTimelineChannelFilter:
    def test_channel_only_in_some_sessions(self, device_store):
        """MeasTemp only exists in session 0, not session 1."""
        result = _query_timeline(
            device_store, "000000000000",
            start_utc_us=1_712_240_400_000_000,
            end_utc_us=1_712_240_710_000_000,
            channel="MeasTemp",
        )
        data_segments = [s for s in result["segments"] if s.get("type") != "gap"]
        assert len(data_segments) == 1
        assert data_segments[0]["session_index"] == 0

    def test_nonexistent_channel(self, device_store):
        """Query for channel that doesn't exist in any session."""
        result = _query_timeline(
            device_store, "000000000000",
            start_utc_us=1_712_240_400_000_000,
            end_utc_us=1_712_240_710_000_000,
            channel="MeasFake",
        )
        data_segments = [s for s in result["segments"] if s.get("type") != "gap"]
        assert len(data_segments) == 0


class TestTimelineResampling:
    def test_target_rate_resamples(self, device_store):
        """Query with target_rate should resample all segments to that rate."""
        result = _query_timeline(
            device_store, "000000000000",
            start_utc_us=1_712_240_400_000_000,
            end_utc_us=1_712_240_710_000_000,
            channel="MeasEcgmV",
            target_rate=100,
        )
        data_segments = [s for s in result["segments"] if s.get("type") != "gap"]
        for seg in data_segments:
            assert seg.get("rate_hz") == 100
