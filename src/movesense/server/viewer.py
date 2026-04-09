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
    mode: str = "stored"  # "stored" or "live"


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
        self._utc_base: Optional[float] = None

    async def start(self, stream_manager, serial: str, channels: list[str]):
        """Start receiving live data."""
        self._queue = await stream_manager.add_client()
        await stream_manager.start(serial, channels)
        self.is_streaming = True
        self._utc_base = None  # Reset: set on first data packet

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
                channel = msg.get("channel", "")
                values = msg.get("values", [])
                t_seconds = msg.get("timestamp", 0)  # seconds since stream start

                # Set UTC base on first packet
                if self._utc_base is None:
                    self._utc_base = time.time() - t_seconds

                # Estimate rate from channel path
                import re
                rate = 200
                m = re.search(r'/(\d+)/', channel)
                if m:
                    rate = int(m.group(1))
                elif 'hr' in channel.lower() or 'temp' in channel.lower():
                    rate = 1

                # Build time array: packet timestamp + sample offset
                n_samples = len(values) if not isinstance(values[0], list) else len(values)
                dt = 1.0 / rate
                base_utc = self._utc_base + t_seconds
                time_arr = [base_utc + i * dt for i in range(n_samples)]

                return {
                    "type": "data",
                    "channel": channel,
                    "time": time_arr,
                    "values": values,
                    "source": "live",
                    "prefetch": False,
                }
            elif msg.get("type") in ("status", "device_info"):
                return None
            return None
        except asyncio.TimeoutError:
            return None


class ViewerHandler:
    """Manages a single WebSocket viewer client."""

    def __init__(self, ws: WebSocket, data_dir: Path, stream_manager=None):
        self.ws = ws
        self.data_dir = data_dir
        self.state = ViewState()
        self.stored = StoredDataSource(data_dir)
        self.live = LiveDataSource()
        self._stream_manager = stream_manager
        self._running = False
        self._device_connected = False
        self._stream_channels = []  # channels for live streaming (independent of logging)
        self._pending_confirm = None  # callback for confirm response

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

        elif msg_type == "mode":
            mode = msg.get("mode", "stored")
            if mode == "live":
                await self._switch_to_live()
            else:
                await self._switch_to_stored()

        elif msg_type == "device_connect":
            await self._device_connect(msg.get("serial", self.state.serial))

        elif msg_type == "device_disconnect":
            self._device_connected = False
            await self._send({"type": "device_status", "connected": False})

        elif msg_type == "device_config":
            await self._device_config(msg.get("paths", []))

        elif msg_type == "device_start":
            await self._device_start()

        elif msg_type == "device_stop":
            await self._device_stop()

        elif msg_type == "device_fetch":
            await self._device_fetch()

        elif msg_type == "device_erase":
            await self._device_erase()

        elif msg_type == "stream_config":
            self._stream_channels = msg.get("channels", [])

        elif msg_type == "confirm_response":
            if self._pending_confirm:
                cb = self._pending_confirm
                self._pending_confirm = None
                await cb(msg.get("confirmed", False))

        elif msg_type == "export":
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

    # --- Mode switching ---

    async def _switch_to_live(self):
        if not self.state.serial or not self._stream_manager:
            await self._send({"type": "error", "message": "Connect device in Settings first"})
            return
        if not self._device_connected:
            await self._send({"type": "error", "message": "Device not connected"})
            return
        channels = self._stream_channels
        if not channels:
            await self._send({"type": "error", "message": "Configure stream channels in Settings first"})
            return
        await self._send({"type": "busy", "message": "Starting live stream..."})
        try:
            await asyncio.wait_for(
                self.live.start(self._stream_manager, self.state.serial, channels),
                timeout=15.0,
            )
            self.state.mode = "live"
            await self._send({"type": "mode_changed", "mode": "live", "streaming_channels": channels})
        except asyncio.TimeoutError:
            await self._send({"type": "error", "message": "BLE connection timed out (15s). Is the device nearby?"})
        except Exception as e:
            await self._send({"type": "error", "message": f"Failed to start stream: {e}"})
        finally:
            await self._send({"type": "busy_done"})

    async def _switch_to_stored(self):
        if self.live.is_streaming and self._stream_manager:
            await self.live.stop(self._stream_manager)
        self.state.mode = "stored"
        await self._send({"type": "mode_changed", "mode": "stored"})
        await self._push_data()

    # --- Device control ---

    async def _device_connect(self, serial: str):
        from ..sensor import SensorCommand
        self.state.serial = serial
        await self._send({"type": "busy", "message": "Connecting to device..."})
        try:
            async with SensorCommand(serial, set_time=False) as sensor:
                status = await sensor.get_status()
                battery = await sensor.get_battery_level()
                status.update(battery)

                # Read config count
                config_count = 0
                config_paths = []
                try:
                    cfg = await sensor.get_resource("/Mem/DataLogger/Config")
                    if cfg.get("success") and cfg.get("data"):
                        config_count = cfg["data"][0] if cfg["data"] else 0
                except Exception:
                    pass

                # Read audit log for last known paths
                audit_file = self.data_dir / serial / "audit.jsonl"
                if config_count > 0 and audit_file.exists():
                    import json as _json
                    for line in reversed(audit_file.read_text().strip().split("\n")):
                        try:
                            entry = _json.loads(line)
                            if entry.get("action") == "config_change":
                                config_paths = entry.get("new_paths", [])
                                break
                        except Exception:
                            continue

                # Get log count and memory status
                log_count = 0
                total_log_bytes = 0
                memory_full = False
                try:
                    log_list = await sensor.get_log_list()
                    if log_list.get("success"):
                        entries = log_list.get("entries", [])
                        log_count = len(entries)
                        total_log_bytes = sum(e.get("size", 0) for e in entries)
                except Exception:
                    pass
                try:
                    full_result = await sensor.get_resource("/Mem/Logbook/IsFull")
                    if full_result.get("success") and full_result.get("data"):
                        memory_full = bool(full_result["data"][0])
                except Exception:
                    pass

                flash_capacity = 128 * 1024 * 1024  # 128 MB
                memory_pct = round(total_log_bytes / flash_capacity * 100, 1) if flash_capacity else 0

                # Probe capabilities
                capabilities = {}
                for sid, rates in [
                    ("ecg", [125, 128, 200, 250, 256, 500, 512]),
                    ("acc", [13, 26, 52, 104, 208, 416, 833]),
                    ("gyro", [13, 26, 52, 104, 208, 416, 833]),
                    ("magn", [13, 26, 52, 104, 208]),
                    ("imu6", [13, 26, 52, 104, 208, 416, 833]),
                    ("imu9", [13, 26, 52, 104, 208, 416, 833]),
                    ("temp", []),
                    ("hr", []),
                ]:
                    capabilities[sid] = {"rates": rates}

                self._device_connected = True
                device_status = {
                    "type": "device_status",
                    "serial": status.get("serial_number", serial),
                    "connected": True,
                    "battery": status.get("battery_level"),
                    "firmware": status.get("app_version", "unknown"),
                    "dlstate": status.get("dlstate", 1),
                    "dlstate_name": {1: "Unknown", 2: "Ready", 3: "Logging"}.get(status.get("dlstate", 1), "Unknown"),
                    "memory_pct": memory_pct,
                    "memory_full": memory_full,
                    "log_count": log_count,
                    "total_log_bytes": total_log_bytes,
                    "logging_config": {"count": config_count, "paths": config_paths},
                    "capabilities": capabilities,
                }
                await self._send(device_status)
        except Exception as e:
            await self._send({"type": "error", "message": f"Device connection failed: {e}"})
        finally:
            await self._send({"type": "busy_done"})

    async def _device_config(self, paths: list[str]):
        from ..sensor import SensorCommand
        if not self.state.serial:
            await self._send({"type": "error", "message": "No device connected"})
            return
        await self._send({"type": "busy", "message": "Applying configuration..."})
        try:
            async with SensorCommand(self.state.serial) as sensor:
                if "/Time/Detailed" not in paths:
                    paths.append("/Time/Detailed")
                config_data = bytearray()
                for p in paths:
                    config_data.extend(p.encode("utf-8") + b"\0")
                result = await sensor.configure_device(config_data)
                if not result.get("success"):
                    await self._send({"type": "error", "message": f"Config failed: {result.get('error')}"})
                else:
                    await self._send({"type": "device_status", "logging_config": {"paths": paths, "count": len(paths)}})
        except Exception as e:
            await self._send({"type": "error", "message": str(e)})
        finally:
            await self._send({"type": "busy_done"})

    async def _device_start(self):
        from ..sensor import SensorCommand
        await self._send({"type": "busy", "message": "Starting recording..."})
        try:
            async with SensorCommand(self.state.serial) as sensor:
                result = await sensor.start_logging()
                if result.get("success"):
                    await self._send({"type": "device_status", "dlstate": 3, "dlstate_name": "Logging"})
                else:
                    await self._send({"type": "error", "message": f"Start failed: {result.get('error')}"})
        except Exception as e:
            await self._send({"type": "error", "message": str(e)})
        finally:
            await self._send({"type": "busy_done"})

    async def _device_stop(self):
        # Require confirmation
        await self._send({"type": "confirm", "title": "Stop Recording?",
                          "body": "This will introduce a gap in the data. Previously recorded data is preserved."})
        async def on_confirm(confirmed):
            if not confirmed:
                return
            from ..sensor import SensorCommand
            await self._send({"type": "busy", "message": "Stopping recording..."})
            try:
                async with SensorCommand(self.state.serial) as sensor:
                    result = await sensor.stop_logging()
                    if result.get("success"):
                        await self._send({"type": "device_status", "dlstate": 2, "dlstate_name": "Ready"})
                    else:
                        await self._send({"type": "error", "message": f"Stop failed: {result.get('error')}"})
            except Exception as e:
                await self._send({"type": "error", "message": str(e)})
            finally:
                await self._send({"type": "busy_done"})
        self._pending_confirm = on_confirm

    async def _device_fetch(self):
        from ..cli import _fetch
        await self._send({"type": "busy", "message": "Downloading logs from device..."})
        try:
            out_dir = self.data_dir / self.state.serial / "fetch_tmp"
            out_dir.mkdir(parents=True, exist_ok=True)
            result = await _fetch(self.state.serial, out_dir, edf=False)
            if result.get("success"):
                await self._send({"type": "busy", "message": "Refreshing data..."})
                # Refresh metadata and push to client
                metadata = self.stored.get_metadata(self.state.serial)
                await self._send(metadata)
                await self._push_data()
            else:
                await self._send({"type": "error", "message": result.get("error", "Fetch failed")})
        except Exception as e:
            await self._send({"type": "error", "message": str(e)})
        finally:
            await self._send({"type": "busy_done"})

    async def _device_erase(self):
        await self._send({"type": "confirm", "title": "Erase Device Memory",
                          "body": "This will permanently delete ALL logged data from the device.",
                          "require_text": "erase"})
        async def on_confirm(confirmed):
            if not confirmed:
                return
            from ..sensor import SensorCommand
            await self._send({"type": "busy", "message": "Erasing device memory..."})
            try:
                async with SensorCommand(self.state.serial, set_time=False) as sensor:
                    result = await sensor.erase_memory()
                    if result.get("success"):
                        await self._send({"type": "device_status", "memory_pct": 0})
                    else:
                        await self._send({"type": "error", "message": f"Erase failed: {result.get('error')}"})
            except Exception as e:
                await self._send({"type": "error", "message": str(e)})
            finally:
                await self._send({"type": "busy_done"})
        self._pending_confirm = on_confirm

    # --- Messaging ---

    async def _send(self, msg: dict):
        """Send JSON message to client."""
        try:
            await self.ws.send_text(json.dumps(msg))
        except Exception:
            self._running = False
