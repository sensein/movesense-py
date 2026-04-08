"""FastAPI application for serving Movesense sensor data."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import zarr

from .auth import get_active_token, get_or_create_token, set_active_token, verify_token
from .scanner import DataScanner
from .manifest import DataManifest
from .stream import StreamManager

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(data_dir: Path) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Movensense Data Server", version="2026.04.04")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Disable browser caching for static files during development
    from starlette.middleware.base import BaseHTTPMiddleware
    class NoCacheStaticMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                response.headers["Pragma"] = "no-cache"
            return response
    app.add_middleware(NoCacheStaticMiddleware)

    scanner = DataScanner(data_dir)
    scanner.scan()

    manifest = DataManifest(data_dir)
    if not manifest.entries:
        manifest.rebuild_from_disk()

    token = get_or_create_token()
    set_active_token(token)
    app.state.token = token
    app.state.scanner = scanner
    app.state.manifest = manifest

    # --- Audit helper ---

    def _audit(serial: str, action: str, detail: dict | None = None, success: bool = True):
        """Append an audit entry to {data_dir}/{serial}/audit.jsonl."""
        try:
            audit_dir = data_dir / serial
            audit_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "serial": serial,
                "success": success,
            }
            if detail:
                entry.update(detail)
            with open(audit_dir / "audit.jsonl", "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            log.warning(f"Failed to write audit entry for {action}")

    # --- Public endpoints ---

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # --- Protected endpoints ---

    @app.get("/api/devices")
    async def list_devices(_: str = Depends(verify_token)):
        return {"devices": scanner.devices}

    @app.get("/api/devices/{serial}/dates")
    async def list_dates(serial: str, _: str = Depends(verify_token)):
        dates = scanner.get_dates(serial)
        if dates is None:
            raise HTTPException(404, detail=f"Device not found: {serial}")
        return {"serial": serial, "dates": dates}

    @app.get("/api/devices/{serial}/dates/{date}/sessions")
    async def list_sessions(serial: str, date: str, _: str = Depends(verify_token)):
        sessions = scanner.get_sessions(serial, date)
        if sessions is None:
            raise HTTPException(404, detail=f"No sessions for {serial} on {date}")
        return {
            "serial": serial,
            "date": date,
            "sessions": [
                {"log_id": s["log_id"], "channels": s["channels"], "has_csv": s["has_csv"], "has_json": s["has_json"]}
                for s in sessions
            ],
        }

    @app.get("/api/devices/{serial}/dates/{date}/sessions/{log_id}/channels")
    async def list_channels(serial: str, date: str, log_id: int, _: str = Depends(verify_token)):
        channels = scanner.get_channels(serial, date, log_id)
        if channels is None:
            raise HTTPException(404, detail=f"Session not found: log {log_id}")
        result = {"channels": channels}
        # Include rich session metadata if available
        meta = scanner.get_session_metadata(serial, date, log_id) or {}
        for key in ("timestamp_mapping", "firmware_version", "device_serial", "channels"):
            if key in meta and key != "channels":  # avoid collision with channel list
                result[key] = meta[key]
        return result

    @app.get("/api/devices/{serial}/dates/{date}/sessions/{log_id}/channels/{channel_name}/data")
    async def get_channel_data(
        serial: str, date: str, log_id: int, channel_name: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(10000, ge=1, le=100000),
        _: str = Depends(verify_token),
    ):
        result = scanner.get_channel_data(serial, date, log_id, channel_name, offset=offset, limit=limit)
        if result is None:
            raise HTTPException(404, detail=f"Channel not found: {channel_name}")
        return result

    @app.get("/api/devices/{serial}/dates/{date}/sessions/{log_id}/metadata")
    async def get_session_metadata(serial: str, date: str, log_id: int, _: str = Depends(verify_token)):
        meta = scanner.get_session_metadata(serial, date, log_id)
        if meta is None:
            raise HTTPException(404, detail=f"Session not found: log {log_id}")
        return meta

    @app.get("/api/timeline")
    async def timeline(serial: str = Query(None), _: str = Depends(verify_token)):
        """Get recordings as a time-based list (not folder-based)."""
        entries = manifest.get_time_ranges(serial=serial)
        return {"recordings": entries}

    @app.post("/api/manifest/rebuild")
    async def rebuild_manifest(_: str = Depends(verify_token)):
        """Rebuild manifest from disk (rescans all Zarr stores)."""
        manifest.rebuild_from_disk()
        return {"status": "rebuilt", "entries": len(manifest.entries)}

    @app.post("/api/refresh")
    async def refresh(_: str = Depends(verify_token)):
        scanner.scan()
        total_sessions = sum(
            len(sessions)
            for dates in scanner._index.values()
            for sessions in dates.values()
        )
        return {"status": "refreshed", "devices": len(scanner.devices), "sessions": total_sessions}

    # --- Device Control (Live Stream tab) ---

    def _check_streaming(serial: str):
        """Raise if a live stream is active for this device — BLE allows only one connection."""
        sm = getattr(app.state, "stream_manager", None)
        if sm and sm.state.value == "streaming" and sm.device_serial == serial:
            raise HTTPException(
                409, detail="Device is currently streaming. Stop the live stream first."
            )

    @app.post("/api/device/connect")
    async def device_connect(request: dict, _: str = Depends(verify_token)):
        """Connect to device, return status + current config.
        If a stream is active for this device, return cached info (can't open second BLE connection).
        """
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        if not serial:
            raise HTTPException(400, detail="serial required")

        # If streaming this device, return cached info (BLE is held by StreamManager)
        sm = getattr(app.state, "stream_manager", None)
        if sm and sm.state.value == "streaming" and sm.device_serial == serial:
            cached = getattr(app.state, "_last_connect_info", None)
            if cached and cached.get("serial") == serial:
                return cached
            # No cached info — return minimal info
            return {
                "serial": serial, "product_name": "Movesense", "app_version": "?",
                "battery": None, "datalogger_state": "Streaming", "dlstate": 3,
                "current_config": "", "config_count": 0, "capabilities": {},
                "memory_full": False, "total_log_size_bytes": 0,
            }

        try:
            async with SensorCommand(serial) as sensor:
                status = await sensor.get_status()
                battery = await sensor.get_battery_level()
                status.update(battery)

                # Read current datalogger config (may timeout while logging)
                # Device config: GET only returns count (not paths) per firmware behavior.
                # Read last known config from our audit log instead.
                config_count = 0
                current_config = ""
                try:
                    config_result = await sensor.get_resource("/Mem/DataLogger/Config")
                    if config_result.get("success"):
                        raw = config_result.get("data", b"")
                        if raw and len(raw) >= 1:
                            config_count = raw[0]
                except Exception:
                    pass

                # Check audit log for last configured paths
                serial_str = status.get("serial_number", serial)
                audit_file = data_dir / serial_str / "audit.jsonl"
                if config_count > 0 and audit_file.exists():
                    try:
                        lines = audit_file.read_text().strip().split("\n")
                        for line in reversed(lines):
                            entry = json.loads(line)
                            if entry.get("action") == "config_change":
                                paths = entry.get("new_paths", [])
                                current_config = "\0".join(paths)
                                break
                    except Exception:
                        pass

                # Probe device capabilities from /Info endpoints
                from ..protocol import parse_info_response, SENSOR_FORMATS
                capabilities = {}

                info_probes = [
                    ("ecg", "/Meas/ECG/Info", "/Meas/Ecg/{rate}/mV", "ECG", "mV"),
                    ("acc", "/Meas/Acc/Info", "/Meas/Acc/{rate}", "Accelerometer", "m/s²"),
                    ("gyro", "/Meas/Gyro/Info", "/Meas/Gyro/{rate}", "Gyroscope", "dps"),
                    ("magn", "/Meas/Magn/Info", "/Meas/Magn/{rate}", "Magnetometer", "µT"),
                    ("imu", "/Meas/IMU/Info", None, "IMU", ""),
                    ("hr", "/Meas/HR/Info", "/Meas/HR", "Heart Rate", "bpm"),
                    ("temp", "/Meas/Temp/Info", "/Meas/Temp", "Temperature", "K"),
                ]

                for sid, path, template, label, unit in info_probes:
                    try:
                        r = await sensor.get_resource(path)
                        if r.get("success"):
                            raw = r.get("data", b"")
                            cap = parse_info_response(sid, raw)
                            entry = {
                                "available": True,
                                "label": label,
                                "unit": unit,
                                "rates": cap.sample_rates if cap.sample_rates else [],
                            }
                            if template:
                                entry["path_template"] = template
                            capabilities[sid] = entry
                        else:
                            capabilities[sid] = {"available": False, "label": label}
                    except Exception:
                        capabilities[sid] = {"available": False, "label": label}

                # HR and Temp are standard on all Movesense devices.
                # They have no /Info endpoint and no rate parameter — mark available as fallback.
                for sid, label, template, unit in [
                    ("hr", "Heart Rate", "/Meas/HR", "bpm"),
                    ("temp", "Temperature", "/Meas/Temp", "K"),
                ]:
                    if not capabilities.get(sid, {}).get("available"):
                        capabilities[sid] = {
                            "available": True,
                            "label": label,
                            "path_template": template,
                            "unit": unit,
                            "rates": [],  # no rate parameter for these
                        }

                # IMU6/9 use ACC rates (same IMU hardware)
                imu_rates = capabilities.get("acc", {}).get("rates", [])
                imu_available = capabilities.get("imu", {}).get("available", False) or len(imu_rates) > 0
                for imu_id, imu_label, imu_template in [
                    ("imu6", "IMU 6-axis (Acc+Gyro)", "/Meas/IMU6/{rate}"),
                    ("imu6m", "IMU 6-axis (Acc+Mag)", "/Meas/IMU6m/{rate}"),
                    ("imu9", "IMU 9-axis (Acc+Gyro+Mag)", "/Meas/IMU9/{rate}"),
                ]:
                    capabilities[imu_id] = {
                        "available": imu_available,
                        "label": imu_label,
                        "path_template": imu_template,
                        "rates": imu_rates,
                        "unit": "m/s²+dps+µT" if "9" in imu_id else "m/s²+dps",
                    } if imu_available else {"available": False, "label": imu_label}

                # Check memory status
                is_full = False
                try:
                    full_result = await sensor.get_resource("/Mem/Logbook/IsFull")
                    if full_result.get("success"):
                        raw = full_result.get("data", b"")
                        is_full = bool(raw[0]) if raw else False
                except Exception:
                    pass

                # Get log entries to estimate used space
                total_log_size = 0
                try:
                    log_list = await sensor.get_log_list()
                    if log_list.get("success"):
                        for entry in log_list.get("entries", []):
                            total_log_size += entry.get("size", 0)
                except Exception:
                    pass

                serial_str = status.get("serial_number", serial)
                _audit(serial_str, "connect", {
                    "battery": status.get("battery_level"),
                    "dlstate": status.get("dlstate", 1),
                    "config_count": config_count,
                    "memory_full": is_full,
                })
                result = {
                    "serial": serial_str,
                    "product_name": status.get("product_name", "Unknown"),
                    "app_version": status.get("app_version", "Unknown"),
                    "battery": status.get("battery_level"),
                    "datalogger_state": {1: "Unknown", 2: "Ready", 3: "Logging"}.get(status.get("dlstate", 1), "Unknown"),
                    "dlstate": status.get("dlstate", 1),
                    "current_config": current_config,
                    "config_count": config_count,
                    "capabilities": capabilities,
                    "memory_full": is_full,
                    "total_log_size_bytes": total_log_size,
                }
                app.state._last_connect_info = result
                return result
        except Exception as e:
            msg = str(e)
            _audit(serial, "connect", {"error": msg}, success=False)
            if "not found" in msg.lower():
                raise HTTPException(503, detail=f"Device not found via BLE. Is it nearby and awake? ({msg})")
            raise HTTPException(500, detail=msg)

    @app.post("/api/device/config")
    async def device_config(request: dict, _: str = Depends(verify_token)):
        """Configure device measurement paths. Device must be in Ready state."""
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        paths = request.get("paths", [])
        audit = request.get("audit", {})
        if not serial or not paths:
            raise HTTPException(400, detail="serial and paths required")
        try:
            async with SensorCommand(serial) as sensor:
                if "/Time/Detailed" not in paths:
                    paths.append("/Time/Detailed")
                config_data = bytearray()
                for path in paths:
                    config_data.extend(path.encode("utf-8") + b"\0")
                result = await sensor.configure_device(config_data)
                if not result.get("success"):
                    raise HTTPException(500, detail=f"Config failed: {result.get('error')}")

                _audit(serial, "config_change", {
                    "new_paths": paths,
                    "previous_paths": audit.get("previous", []),
                    "added": audit.get("added", []),
                    "removed": audit.get("removed", []),
                })
                return {"status": "configured", "paths": paths}
        except HTTPException:
            raise
        except Exception as e:
            _audit(serial, "config_change", {"error": str(e)}, success=False)
            raise HTTPException(500, detail=str(e))

    @app.post("/api/device/start")
    async def device_start(request: dict, _: str = Depends(verify_token)):
        """Start logging on device."""
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        if not serial:
            raise HTTPException(400, detail="serial required")
        _check_streaming(serial)
        try:
            async with SensorCommand(serial) as sensor:
                status = await sensor.get_status()
                if status.get("dlstate") == 3:
                    _audit(serial, "start_logging", {"note": "already logging"})
                    return {"status": "already_logging"}
                result = await sensor.start_logging()
                if not result.get("success"):
                    raise HTTPException(500, detail=f"Start failed: {result.get('error')}")
                _audit(serial, "start_logging")
                return {"status": "logging_started"}
        except HTTPException:
            raise
        except Exception as e:
            _audit(serial, "start_logging", {"error": str(e)}, success=False)
            raise HTTPException(500, detail=str(e))

    @app.post("/api/device/fetch")
    async def device_fetch(request: dict, _: str = Depends(verify_token)):
        """Fetch all logs from device → SBEM → JSON → Zarr + CSV. Stores both raw and converted."""
        from ..cli import _fetch
        serial = request.get("serial", "")
        if not serial:
            raise HTTPException(400, detail="serial required")
        _check_streaming(serial)

        out_dir = data_dir / serial / datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            result = await _fetch(serial, out_dir, edf=False)
            if result.get("success"):
                scanner.scan()  # refresh index
                _audit(serial, "fetch", {
                    "log_count": len(result.get("files", [])),
                    "output_dir": str(out_dir),
                })
                return {
                    "status": "fetched",
                    "files": result.get("files", []),
                    "output_dir": str(out_dir),
                    "log_count": len(result.get("files", [])),
                }
            else:
                _audit(serial, "fetch", {"error": result.get("error")}, success=False)
                raise HTTPException(500, detail=result.get("error", "Fetch failed"))
        except HTTPException:
            raise
        except Exception as e:
            _audit(serial, "fetch", {"error": str(e)}, success=False)
            raise HTTPException(500, detail=str(e))

    @app.post("/api/device/erase")
    async def device_erase(request: dict, _: str = Depends(verify_token)):
        """Erase all logs from device memory."""
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        if not serial:
            raise HTTPException(400, detail="serial required")
        _check_streaming(serial)
        try:
            async with SensorCommand(serial, set_time=False) as sensor:
                result = await sensor.erase_memory()
                if not result.get("success"):
                    raise HTTPException(500, detail=f"Erase failed: {result.get('error')}")
                _audit(serial, "erase")
                return {"status": "memory_erased"}
        except HTTPException:
            raise
        except Exception as e:
            _audit(serial, "erase", {"error": str(e)}, success=False)
            raise HTTPException(500, detail=str(e))

    @app.post("/api/device/stop")
    async def device_stop(request: dict, _: str = Depends(verify_token)):
        """Stop logging on device. No reboot — device goes to Ready state."""
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        if not serial:
            raise HTTPException(400, detail="serial required")
        _check_streaming(serial)
        try:
            async with SensorCommand(serial) as sensor:
                result = await sensor.stop_logging()
                if not result.get("success"):
                    raise HTTPException(500, detail=f"Stop failed: {result.get('error')}")
                _audit(serial, "stop_logging")
                return {"status": "logging_stopped"}
        except HTTPException:
            raise
        except Exception as e:
            _audit(serial, "stop_logging", {"error": str(e)}, success=False)
            raise HTTPException(500, detail=str(e))

    @app.get("/api/devices/{serial}/dates/{date}/sessions/{log_id}/window-stats")
    async def window_stats(
        serial: str, date: str, log_id: int,
        start: float = Query(0, ge=0),
        end: float = Query(None),
        _: str = Depends(verify_token),
    ):
        """Compute physio analytics for a time window."""
        import numpy as np
        from movesense.physio.pipeline import analyze_session
        from movesense.physio.ecg import detect_r_peaks, compute_rr_intervals, compute_hrv
        from movesense.physio.quality import ecg_signal_quality

        sessions = scanner.get_sessions(serial, date)
        if not sessions:
            raise HTTPException(404, detail="Session not found")
        session = next((s for s in sessions if s["log_id"] == log_id), None)
        if not session:
            raise HTTPException(404, detail="Session not found")

        import zarr
        try:
            store = zarr.open_group(session["zarr_path"], mode="r")
        except Exception as e:
            raise HTTPException(500, detail=f"Zarr error: {e}")

        result = {"start": start, "end": end, "channels": {}}

        for ch_name in store:
            group = store[ch_name]
            if "data" not in group:
                continue

            arr = group["data"][:]
            rate = group.attrs.get("sampling_rate_hz", 1.0)
            s_idx = max(0, int(start * rate))
            e_idx = int(end * rate) if end else arr.shape[0]
            e_idx = min(e_idx, arr.shape[0])
            chunk = arr[s_idx:e_idx]

            if chunk.size == 0:
                continue

            ch_stats = {"sample_count": int(len(chunk)), "sampling_rate_hz": float(rate)}

            if chunk.ndim == 1:
                ch_stats.update({
                    "min": round(float(np.min(chunk)), 4),
                    "max": round(float(np.max(chunk)), 4),
                    "mean": round(float(np.mean(chunk)), 4),
                    "std": round(float(np.std(chunk)), 4),
                })
            else:
                cols = ["x", "y", "z", "a", "b", "c", "d", "e", "f"][:chunk.shape[1]]
                for i, col in enumerate(cols):
                    ch_stats[f"{col}_min"] = round(float(np.min(chunk[:, i])), 4)
                    ch_stats[f"{col}_max"] = round(float(np.max(chunk[:, i])), 4)
                    ch_stats[f"{col}_mean"] = round(float(np.mean(chunk[:, i])), 4)
                    ch_stats[f"{col}_std"] = round(float(np.std(chunk[:, i])), 4)
                mag = np.sqrt(np.sum(chunk ** 2, axis=1))
                ch_stats["magnitude_mean"] = round(float(np.mean(mag)), 4)

            # ECG-specific analytics
            sensor_type = group.attrs.get("sensor_type", "")
            if "ecg" in sensor_type.lower() or "ecg" in ch_name.lower():
                if chunk.ndim == 1 and len(chunk) > 10:
                    try:
                        # Try simple_threshold first (more robust on real wearable ECG)
                        peaks = detect_r_peaks(chunk, rate, method="simple_threshold")
                        if len(peaks) < 2:
                            peaks = detect_r_peaks(chunk, rate, method="pan_tompkins")
                        if len(peaks) >= 2:
                            rr = compute_rr_intervals(peaks, rate)
                            hrv = compute_hrv(rr)
                            ch_stats["hr_bpm"] = round(float(hrv["mean_hr"]), 1)
                            ch_stats["hrv_sdnn"] = round(float(hrv["sdnn"]), 2)
                            ch_stats["hrv_rmssd"] = round(float(hrv["rmssd"]), 2)
                            ch_stats["r_peak_count"] = int(len(peaks))
                        sqi = ecg_signal_quality(chunk, rate, window_s=min(5.0, len(chunk) / rate))
                        if sqi:
                            ch_stats["sqi"] = round(float(sqi[0]["sqi"]), 3)
                            ch_stats["sqi_level"] = str(sqi[0]["level"])
                    except Exception:
                        pass

            # Activity classification for ACC
            if chunk.ndim == 2 and chunk.shape[1] >= 3:
                try:
                    from movesense.physio.motion import classify_activity
                    labels = classify_activity(chunk, rate)
                    if len(labels) > 0:
                        activity_pct = round(float(100 * sum(1 for l in labels if l == "activity") / len(labels)), 1)
                        ch_stats["activity_pct"] = activity_pct
                except Exception:
                    pass

            result["channels"][ch_name] = ch_stats

        return result

    @app.get("/api/devices/{serial}/dates/{date}/sessions/{log_id}/channels/{channel_name}/downsample")
    async def downsample_channel(
        serial: str, date: str, log_id: int, channel_name: str,
        start: float = Query(0, ge=0),
        end: float = Query(None),
        buckets: int = Query(1000, ge=1, le=10000),
        _: str = Depends(verify_token),
    ):
        result = scanner.downsample_channel(serial, date, log_id, channel_name, start=start, end=end, buckets=buckets)
        if result is None:
            raise HTTPException(404, detail=f"Channel not found: {channel_name}")
        return result

    @app.get("/api/devices/{serial}/coverage/{year}/{month}")
    async def get_coverage(serial: str, year: int, month: int, _: str = Depends(verify_token)):
        result = scanner.compute_coverage(serial, year, month)
        if result is None:
            raise HTTPException(404, detail=f"Device not found: {serial}")
        return result

    # --- Timeline API ---

    @app.get("/api/devices/{serial}/timeline")
    async def timeline_query(
        serial: str,
        start: str = Query(..., description="UTC start time (ISO 8601)"),
        end: str = Query(..., description="UTC end time (ISO 8601)"),
        channel: str = Query(None, description="Channel filter"),
        buckets: int = Query(0, ge=0, description="Downsample target (0=raw)"),
        target_rate: float = Query(None, description="Resample to this Hz"),
        _: str = Depends(verify_token),
    ):
        """Query sensor data across sessions by UTC time range."""
        from .timeline import query_timeline
        from datetime import datetime as dt, timezone as tz

        # Parse ISO 8601 to µs
        try:
            start_dt = dt.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = dt.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, detail="Invalid ISO 8601 timestamp")

        start_us = int(start_dt.timestamp() * 1_000_000)
        end_us = int(end_dt.timestamp() * 1_000_000)

        if start_us >= end_us:
            raise HTTPException(400, detail="start must be before end")

        return query_timeline(
            data_dir, serial,
            start_utc_us=start_us,
            end_utc_us=end_us,
            channel=channel,
            buckets=buckets,
            target_rate=target_rate,
        )

    @app.get("/api/devices/{serial}/sessions")
    async def list_device_sessions(serial: str, _: str = Depends(verify_token)):
        """List all recording sessions for a device with enriched metadata."""
        store_path = data_dir / serial / "data.zarr"
        if store_path.exists():
            root = zarr.open_group(str(store_path), mode="r")
            sessions_idx = dict(root.attrs.get("sessions", {}))
            sessions = []
            for idx_str, summary in sorted(sessions_idx.items(), key=lambda x: int(x[0])):
                entry = {"index": int(idx_str)}
                entry.update(summary)
                sessions.append(entry)
            return {"serial": serial, "session_count": len(sessions), "sessions": sessions}
        else:
            # Fallback to scanner for legacy layout
            dates = scanner.get_dates(serial)
            if dates is None:
                raise HTTPException(404, detail=f"Device not found: {serial}")
            sessions = []
            for date in dates:
                for s in scanner.get_sessions(serial, date) or []:
                    sessions.append({"index": s["log_id"], "date": date, "channels": s["channels"]})
            return {"serial": serial, "session_count": len(sessions), "sessions": sessions}

    # --- WebSocket Streaming ---

    stream_manager = StreamManager()
    app.state.stream_manager = stream_manager

    @app.get("/api/stream/status")
    async def stream_status(_: str = Depends(verify_token)):
        """Return current stream state so the UI can recover after refresh."""
        return stream_manager._status_message()

    # --- Viewer WebSocket (server-driven protocol for stored + live data) ---

    @app.websocket("/ws/viewer")
    async def websocket_viewer(ws: WebSocket):
        from .viewer import ViewerHandler

        ws_token = ws.query_params.get("token", "")
        if ws_token != get_active_token():
            await ws.close(code=1008, reason="Authentication failed")
            return

        await ws.accept()
        handler = ViewerHandler(ws, data_dir, stream_manager=stream_manager)
        await handler.run()

    @app.websocket("/ws/stream")
    async def websocket_stream(ws: WebSocket):
        # Validate token from query param
        ws_token = ws.query_params.get("token", "")
        if ws_token != get_active_token():
            await ws.close(code=1008, reason="Authentication failed")
            return

        await ws.accept()
        client_queue = await stream_manager.add_client()

        # Two concurrent tasks: read from client, write to client
        async def reader():
            try:
                while True:
                    raw = await ws.receive_text()
                    msg = json.loads(raw)
                    msg_type = msg.get("type")
                    if msg_type == "start":
                        serial = msg.get("serial", "")
                        channels = msg.get("channels", [])
                        await stream_manager.start(serial, channels)
                        await client_queue.put({"type": "ack", "action": "start", "channels": channels})
                    elif msg_type == "stop":
                        await stream_manager.stop()
                        await client_queue.put({"type": "ack", "action": "stop"})
            except WebSocketDisconnect:
                pass
            except Exception as e:
                log.error(f"WebSocket reader error: {e}")

        async def writer():
            try:
                while True:
                    message = await client_queue.get()
                    await ws.send_text(json.dumps(message))
            except WebSocketDisconnect:
                pass
            except Exception as e:
                log.error(f"WebSocket writer error: {e}")

        try:
            await asyncio.gather(reader(), writer())
        finally:
            stream_manager.remove_client(client_queue)

    # --- Static UI ---

    @app.get("/favicon.ico")
    async def favicon():
        from fastapi.responses import Response
        # 1x1 transparent PNG favicon
        return Response(content=b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82', media_type="image/png")

    @app.get("/viewer", response_class=HTMLResponse)
    async def viewer_page():
        """Server-driven streaming viewer (spec 011)."""
        viewer_html = STATIC_DIR / "viewer.html"
        if viewer_html.exists():
            return viewer_html.read_text()
        return "<h1>Viewer not found</h1>"

    @app.get("/", response_class=HTMLResponse)
    async def root():
        # Default to viewer
        viewer_html = STATIC_DIR / "viewer.html"
        if viewer_html.exists():
            return viewer_html.read_text()
        index = STATIC_DIR / "index.html"
        if index.exists():
            return index.read_text()
        return "<h1>Movensense Data Server</h1><p>No UI installed.</p>"

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
