"""Server-driven viewer: WebSocket protocol handler for stored + live data.

The server pushes data to the UI at the right resolution. The UI is a thin
renderer — it sends view/subscribe/stream control messages, and renders
whatever data it receives.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


@dataclass
class ViewState:
    """Per-client view state maintained on the server."""
    serial: str = ""
    start_us: int = 0
    end_us: int = 0
    width_px: int = 1200
    channels: list[str] = field(default_factory=list)
    last_push_time: float = 0


class StoredDataSource:
    """Reads from DeviceStore Zarr via timeline query."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def get_metadata(self, serial: str) -> dict:
        """Build metadata message from DeviceStore."""
        import zarr
        store_path = self.data_dir / serial / "data.zarr"
        if not store_path.exists():
            return {"type": "metadata", "serial": serial, "channels": [], "time_range": {}, "sessions": [], "state": "idle"}

        root = zarr.open_group(str(store_path), mode="r")
        sessions_idx = dict(root.attrs.get("sessions", {}))

        # Build channel list (union across all sessions)
        all_channels = {}
        for idx_str, summary in sessions_idx.items():
            for ch_name, ch_meta in summary.get("channels", {}).items():
                if ch_name not in all_channels:
                    all_channels[ch_name] = {
                        "name": ch_name,
                        "rate_hz": ch_meta.get("rate_hz"),
                        "unit": ch_meta.get("unit", ""),
                        "axes": ch_meta.get("axes", 1),
                    }

        # Fill missing timestamps from prov log
        import json as _json
        prov_file = store_path.parent / "prov.jsonl"
        prov_dates = {}  # session_index → fetched_at ISO string
        if prov_file.exists():
            for line in prov_file.read_text().strip().split("\n"):
                try:
                    entry = _json.loads(line)
                    si = entry.get("session_index")
                    if si is not None and entry.get("fetched_at"):
                        prov_dates[si] = entry["fetched_at"]
                except Exception:
                    pass

        sessions_list = []
        for idx_str, summary in sorted(sessions_idx.items(), key=lambda x: int(x[0])):
            idx = int(idx_str)
            start_us = summary.get("start_utc_us", 0)
            end_us = summary.get("end_utc_us", 0)

            # Fallback: estimate from prov fetched_at + duration
            if not start_us and idx in prov_dates:
                from datetime import datetime as dt, timezone as tz
                try:
                    fetched = dt.fromisoformat(prov_dates[idx].replace("Z", "+00:00"))
                    start_us = int(fetched.timestamp() * 1_000_000)
                    dur_s = summary.get("duration_seconds", 0)
                    end_us = start_us + int(dur_s * 1_000_000) if dur_s else start_us + 60_000_000  # default 1 min
                except Exception:
                    pass

            sessions_list.append({
                "index": idx,
                "start_us": start_us,
                "end_us": end_us,
                "channels": list(summary.get("channels", {}).keys()),
            })

        start_us_vals = [s["start_us"] for s in sessions_list if s["start_us"] > 0]
        end_us_vals = [s["end_us"] for s in sessions_list if s["end_us"] > 0]

        # Device info from first session group attrs
        device_info = {"name": "Movesense", "firmware": "unknown", "battery": None}
        try:
            first_group = root[list(sessions_idx.keys())[0]] if sessions_idx else None
            if first_group:
                device_info["firmware"] = first_group.attrs.get("firmware_version", "unknown")
                device_info["name"] = first_group.attrs.get("device_serial", serial)
        except Exception:
            pass

        return {
            "type": "metadata",
            "serial": serial,
            "device": device_info,
            "channels": list(all_channels.values()),
            "time_range": {
                "start_us": min(start_us_vals) if start_us_vals else 0,
                "end_us": max(end_us_vals) if end_us_vals else 0,
            },
            "sessions": sessions_list,
            "state": "idle",
        }

    def query(self, serial: str, start_us: int, end_us: int, channel: str, buckets: int) -> Optional[dict]:
        """Query stored data for a channel in a time range.

        Returns time as absolute UTC seconds (for calendar-aware X-axis).
        """
        from .timeline import query_timeline

        result = query_timeline(
            self.data_dir, serial,
            start_utc_us=start_us,
            end_utc_us=end_us,
            channel=channel,
            buckets=buckets,
        )

        if not result or not result.get("segments"):
            return None

        # Merge segments into a single data packet with absolute UTC time
        all_time = []
        all_values = []
        for seg in result["segments"]:
            if seg.get("type") == "gap":
                continue
            data = seg.get("data", {})
            seg_start_us = seg.get("start_utc_us", 0)
            if data.get("time"):
                # Convert relative seconds to absolute UTC seconds
                for t in data["time"]:
                    utc_s = seg_start_us / 1_000_000 + t
                    all_time.append(round(utc_s, 6))
                if data.get("values"):
                    all_values.extend(data["values"])
                elif data.get("columns"):
                    for i in range(len(data["time"])):
                        row = []
                        for col in data["columns"]:
                            col_data = data.get(col, [])
                            row.append(col_data[i] if i < len(col_data) else 0)
                        all_values.append(row)

        if not all_time:
            return None

        # Sort by time and insert nulls at gaps between sessions
        if len(all_time) > 1:
            # Pair time+values, sort by time
            paired = sorted(zip(all_time, all_values), key=lambda x: x[0])
            sorted_time = []
            sorted_values = []
            for i, (t, v) in enumerate(paired):
                if i > 0:
                    dt = t - paired[i-1][0]
                    # Insert null gap marker if gap > 10 seconds
                    if dt > 10:
                        # Add null point just after previous and just before current
                        sorted_time.append(paired[i-1][0] + 0.001)
                        sorted_values.append(None)
                        sorted_time.append(t - 0.001)
                        sorted_values.append(None)
                sorted_time.append(t)
                sorted_values.append(v)
            all_time = sorted_time
            all_values = sorted_values

        return {
            "type": "data",
            "channel": channel,
            "time": all_time,
            "values": all_values,
            "source": "store",
            "prefetch": False,
        }


class LiveDataSource:
    """Receives live BLE data from StreamManager."""

    def __init__(self):
        self.is_streaming = False
        self._queue: Optional[asyncio.Queue] = None

    async def start(self, stream_manager, serial: str, channels: list[str]):
        """Start receiving live data."""
        self._queue = await stream_manager.add_client()
        await stream_manager.start(serial, channels)
        self.is_streaming = True

    async def stop(self, stream_manager):
        """Stop receiving live data."""
        if self._queue:
            stream_manager.remove_client(self._queue)
        await stream_manager.stop()
        self.is_streaming = False
        self._queue = None

    async def get_next(self) -> Optional[dict]:
        """Get next data packet from BLE stream."""
        if not self._queue:
            return None
        try:
            msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            if msg.get("type") == "data":
                return {
                    "type": "data",
                    "channel": msg["channel"],
                    "time": [msg.get("timestamp", 0)],
                    "values": msg.get("values", []),
                    "source": "live",
                    "prefetch": False,
                }
            return msg  # status, error, etc.
        except asyncio.TimeoutError:
            return None


class ViewerHandler:
    """Manages a single WebSocket viewer client."""

    def __init__(self, ws: WebSocket, data_dir: Path, stream_manager=None):
        self.ws = ws
        self.state = ViewState()
        self.stored = StoredDataSource(data_dir)
        self.live = LiveDataSource()
        self._stream_manager = stream_manager
        self._running = False

    async def run(self):
        """Main message loop."""
        self._running = True

        # Two concurrent tasks: read client messages + forward live data
        async def reader():
            try:
                while self._running:
                    raw = await self.ws.receive_text()
                    msg = json.loads(raw)
                    await self._handle_message(msg)
            except WebSocketDisconnect:
                self._running = False
            except Exception as e:
                log.error(f"Viewer reader error: {e}")
                self._running = False

        async def live_forwarder():
            try:
                while self._running:
                    if self.live.is_streaming:
                        pkt = await self.live.get_next()
                        if pkt:
                            await self._send(pkt)
                    else:
                        await asyncio.sleep(0.1)
            except Exception as e:
                log.error(f"Viewer live forwarder error: {e}")

        try:
            await asyncio.gather(reader(), live_forwarder())
        finally:
            if self.live.is_streaming and self._stream_manager:
                await self.live.stop(self._stream_manager)

    async def _handle_message(self, msg: dict):
        msg_type = msg.get("type")

        if msg_type == "connect":
            self.state.serial = msg.get("serial", "")
            # Push metadata
            metadata = self.stored.get_metadata(self.state.serial)
            await self._send(metadata)
            # Set default view to full range and push overview
            tr = metadata.get("time_range", {})
            if tr.get("start_us") and tr.get("end_us"):
                self.state.start_us = tr["start_us"]
                self.state.end_us = tr["end_us"]
                self.state.channels = [c["name"] for c in metadata.get("channels", [])]
                await self._push_data()

        elif msg_type == "view":
            self.state.start_us = msg.get("start_us", self.state.start_us)
            self.state.end_us = msg.get("end_us", self.state.end_us)
            self.state.width_px = msg.get("width_px", self.state.width_px)
            await self._push_data()
            await self._push_prefetch()

        elif msg_type == "subscribe":
            self.state.channels = msg.get("channels", self.state.channels)
            await self._push_data()

        elif msg_type == "stream":
            action = msg.get("action")
            if action == "start" and self._stream_manager:
                channels = msg.get("channels", [])
                await self.live.start(self._stream_manager, self.state.serial, channels)
                await self._send({"type": "status", "state": "streaming"})
            elif action == "stop" and self._stream_manager:
                await self.live.stop(self._stream_manager)
                await self._send({"type": "status", "state": "idle"})

        elif msg_type == "export":
            # TODO: implement export in future spec
            await self._send({"type": "error", "message": "Export not yet implemented"})

    async def _push_data(self):
        """Push data for current view state."""
        if not self.state.serial or not self.state.channels:
            return
        buckets = max(100, self.state.width_px)
        for ch in self.state.channels:
            pkt = self.stored.query(
                self.state.serial, self.state.start_us, self.state.end_us,
                ch, buckets,
            )
            if pkt:
                await self._send(pkt)
        self.state.last_push_time = time.monotonic()

    async def _push_prefetch(self):
        """Push adjacent windows for instant panning."""
        if not self.state.serial or not self.state.channels:
            return
        window = self.state.end_us - self.state.start_us
        if window <= 0:
            return
        buckets = max(100, self.state.width_px)

        for offset in [-window, window]:  # prev and next
            pf_start = self.state.start_us + offset
            pf_end = self.state.end_us + offset
            if pf_start < 0:
                continue
            for ch in self.state.channels:
                pkt = self.stored.query(self.state.serial, pf_start, pf_end, ch, buckets)
                if pkt:
                    pkt["prefetch"] = True
                    await self._send(pkt)

    async def _send(self, msg: dict):
        """Send JSON message to client."""
        try:
            await self.ws.send_text(json.dumps(msg))
        except Exception:
            self._running = False
