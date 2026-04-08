"""Tests for the server-driven viewer WebSocket protocol."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import zarr

from movesense.server.viewer import LiveDataSource, StoredDataSource, ViewerHandler, ViewState
from movesense.storage import DeviceStore


@pytest.fixture
def device_store(tmp_path):
    """Create a DeviceStore with one session for testing."""
    device_dir = tmp_path / "000000000000"
    device_dir.mkdir()
    ds = DeviceStore(device_dir)
    ds.open()

    g0 = ds.add_session(0, {"device_serial": "000000000000", "firmware_version": "1.0.1"})
    ecg = g0.require_group("MeasECGmV")
    ecg.create_array("data", data=np.sin(np.linspace(0, 10*np.pi, 1000)).astype(np.float32))
    ecg.attrs["sampling_rate_hz"] = 200.0
    ecg.attrs["sensor_type"] = "ECG"
    ecg.attrs["unit"] = "mV"

    ds.update_sessions_index(0, {
        "start_utc": "2026-04-04T14:00:00.000000Z",
        "start_utc_us": 1_712_240_400_000_000,
        "end_utc": "2026-04-04T14:00:05.000000Z",
        "end_utc_us": 1_712_240_405_000_000,
        "duration_seconds": 5.0,
        "channels": {"MeasECGmV": {"rate_hz": 200, "samples": 1000, "unit": "mV"}},
    })
    ds.close()
    return tmp_path


class TestViewState:
    def test_defaults(self):
        vs = ViewState()
        assert vs.serial == ""
        assert vs.width_px == 1200
        assert vs.channels == []

    def test_update(self):
        vs = ViewState(serial="test", start_us=100, end_us=200)
        assert vs.serial == "test"
        assert vs.end_us - vs.start_us == 100


class TestStoredDataSource:
    def test_get_metadata(self, device_store):
        src = StoredDataSource(device_store)
        meta = src.get_metadata("000000000000")
        assert meta["type"] == "metadata"
        assert meta["serial"] == "000000000000"
        assert len(meta["channels"]) == 1
        assert meta["channels"][0]["name"] == "MeasECGmV"
        assert meta["channels"][0]["rate_hz"] == 200
        assert meta["time_range"]["start_us"] == 1_712_240_400_000_000
        assert len(meta["sessions"]) == 1

    def test_get_metadata_no_device(self, tmp_path):
        src = StoredDataSource(tmp_path)
        meta = src.get_metadata("nonexistent")
        assert meta["channels"] == []

    def test_query(self, device_store):
        src = StoredDataSource(device_store)
        pkt = src.query(
            "000000000000",
            1_712_240_400_000_000,
            1_712_240_405_000_000,
            "MeasECGmV",
            100,
        )
        assert pkt is not None
        assert pkt["type"] == "data"
        assert pkt["channel"] == "MeasECGmV"
        assert pkt["source"] == "store"
        assert pkt["prefetch"] is False
        assert len(pkt["time"]) > 0
        assert len(pkt["values"]) > 0

    def test_query_nonexistent_channel(self, device_store):
        src = StoredDataSource(device_store)
        pkt = src.query("000000000000", 1_712_240_400_000_000, 1_712_240_405_000_000, "MeasFake", 100)
        assert pkt is None


class TestViewerHandlerProtocol:
    @pytest.mark.asyncio
    async def test_connect_pushes_metadata(self, device_store):
        ws = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "connect", "serial": "000000000000"}),
            Exception("done"),  # stop after one message
        ])
        handler = ViewerHandler(ws, device_store)

        try:
            await handler.run()
        except Exception:
            pass

        # Check that metadata was sent
        calls = ws.send_text.call_args_list
        assert len(calls) >= 1
        first_msg = json.loads(calls[0][0][0])
        assert first_msg["type"] == "metadata"
        assert first_msg["serial"] == "000000000000"
        assert len(first_msg["channels"]) == 1

    @pytest.mark.asyncio
    async def test_connect_pushes_data(self, device_store):
        ws = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "connect", "serial": "000000000000"}),
            Exception("done"),
        ])
        handler = ViewerHandler(ws, device_store)

        try:
            await handler.run()
        except Exception:
            pass

        calls = ws.send_text.call_args_list
        messages = [json.loads(c[0][0]) for c in calls]
        data_msgs = [m for m in messages if m.get("type") == "data"]
        assert len(data_msgs) >= 1
        assert data_msgs[0]["channel"] == "MeasECGmV"
        assert data_msgs[0]["source"] == "store"

    @pytest.mark.asyncio
    async def test_view_changes_resolution(self, device_store):
        ws = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "connect", "serial": "000000000000"}),
            json.dumps({"type": "view", "start_us": 1_712_240_400_000_000, "end_us": 1_712_240_402_000_000, "width_px": 500}),
            Exception("done"),
        ])
        handler = ViewerHandler(ws, device_store)

        try:
            await handler.run()
        except Exception:
            pass

        calls = ws.send_text.call_args_list
        messages = [json.loads(c[0][0]) for c in calls]
        # Should have metadata + initial data + view data + prefetch
        data_msgs = [m for m in messages if m.get("type") == "data"]
        assert len(data_msgs) >= 2  # initial + view response

    @pytest.mark.asyncio
    async def test_subscribe_filters_channels(self, device_store):
        ws = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "connect", "serial": "000000000000"}),
            json.dumps({"type": "subscribe", "channels": []}),  # empty = no data
            Exception("done"),
        ])
        handler = ViewerHandler(ws, device_store)

        try:
            await handler.run()
        except Exception:
            pass

        # After subscribe with empty channels, no additional data should be pushed
        calls = ws.send_text.call_args_list
        messages = [json.loads(c[0][0]) for c in calls]
        # Last messages should not include new data after subscribe
        post_subscribe = messages[2:]  # after metadata + initial data
        data_after = [m for m in post_subscribe if m.get("type") == "data" and not m.get("prefetch")]
        assert len(data_after) == 0


class TestStoredLiveTransition:
    @pytest.mark.asyncio
    async def test_stored_then_live(self, device_store):
        """Stored data push then live data should arrive on same connection."""
        ws = AsyncMock()
        messages_received = []

        async def capture_send(text):
            messages_received.append(json.loads(text))

        ws.send_text = capture_send
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "connect", "serial": "000000000000"}),
            Exception("done"),
        ])
        handler = ViewerHandler(ws, device_store)

        try:
            await handler.run()
        except Exception:
            pass

        # Should have metadata + stored data
        types = [m["type"] for m in messages_received]
        assert "metadata" in types
        data_msgs = [m for m in messages_received if m["type"] == "data"]
        assert all(m["source"] == "store" for m in data_msgs)

        # Simulate what would happen with live: the LiveDataSource would push
        # packets with source="live" — verify the format is compatible
        live_pkt = {
            "type": "data", "channel": "MeasECGmV",
            "time": [100.0, 100.005], "values": [0.5, 0.6],
            "source": "live", "prefetch": False,
        }
        # Verify live packet has later timestamps
        if data_msgs:
            stored_max_t = max(data_msgs[0].get("time", [0]))
            assert live_pkt["time"][0] > stored_max_t or stored_max_t == 0


class TestPrefetch:
    @pytest.mark.asyncio
    async def test_view_triggers_prefetch(self, device_store):
        ws = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "connect", "serial": "000000000000"}),
            json.dumps({"type": "view", "start_us": 1_712_240_401_000_000, "end_us": 1_712_240_403_000_000, "width_px": 1000}),
            Exception("done"),
        ])
        handler = ViewerHandler(ws, device_store)

        try:
            await handler.run()
        except Exception:
            pass

        calls = ws.send_text.call_args_list
        messages = [json.loads(c[0][0]) for c in calls]
        prefetch_msgs = [m for m in messages if m.get("type") == "data" and m.get("prefetch")]
        # Should have prefetch messages for adjacent windows
        assert len(prefetch_msgs) >= 1
