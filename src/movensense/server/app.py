"""FastAPI application for serving Movesense sensor data."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .auth import get_active_token, get_or_create_token, set_active_token, verify_token
from .scanner import DataScanner
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

    scanner = DataScanner(data_dir)
    scanner.scan()

    token = get_or_create_token()
    set_active_token(token)
    app.state.token = token
    app.state.scanner = scanner

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
        return {"channels": channels}

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

    @app.post("/api/device/connect")
    async def device_connect(request: dict, _: str = Depends(verify_token)):
        """Connect to device, return status + current config."""
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        if not serial:
            raise HTTPException(400, detail="serial required")
        try:
            async with SensorCommand(serial) as sensor:
                status = await sensor.get_status()
                battery = await sensor.get_battery_level()
                status.update(battery)

                # Read current datalogger config
                config_result = await sensor.get_resource("/Mem/DataLogger/Config")
                current_config = ""
                if config_result.get("success"):
                    raw = config_result.get("data", b"")
                    if raw:
                        current_config = raw.decode("utf-8", errors="ignore").rstrip("\x00")

                return {
                    "serial": status.get("serial_number", serial),
                    "product_name": status.get("product_name", "Unknown"),
                    "app_version": status.get("app_version", "Unknown"),
                    "battery": status.get("battery_level"),
                    "datalogger_state": {1: "Unknown", 2: "Ready", 3: "Logging"}.get(status.get("dlstate", 1), "Unknown"),
                    "dlstate": status.get("dlstate", 1),
                    "current_config": current_config,
                }
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.post("/api/device/config")
    async def device_config(request: dict, _: str = Depends(verify_token)):
        """Configure device measurement paths. Device must be in Ready state."""
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        paths = request.get("paths", [])
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
                return {"status": "configured", "paths": paths}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.post("/api/device/start")
    async def device_start(request: dict, _: str = Depends(verify_token)):
        """Start logging on device."""
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        if not serial:
            raise HTTPException(400, detail="serial required")
        try:
            async with SensorCommand(serial) as sensor:
                status = await sensor.get_status()
                if status.get("dlstate") == 3:
                    return {"status": "already_logging"}
                result = await sensor.start_logging()
                if not result.get("success"):
                    raise HTTPException(500, detail=f"Start failed: {result.get('error')}")
                return {"status": "logging_started"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.post("/api/device/stop")
    async def device_stop(request: dict, _: str = Depends(verify_token)):
        """Stop logging on device. No reboot — device goes to Ready state."""
        from ..sensor import SensorCommand
        serial = request.get("serial", "")
        if not serial:
            raise HTTPException(400, detail="serial required")
        try:
            async with SensorCommand(serial) as sensor:
                result = await sensor.stop_logging()
                if not result.get("success"):
                    raise HTTPException(500, detail=f"Stop failed: {result.get('error')}")
                return {"status": "logging_stopped"}
        except HTTPException:
            raise
        except Exception as e:
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
        from movensense.physio.pipeline import analyze_session
        from movensense.physio.ecg import detect_r_peaks, compute_rr_intervals, compute_hrv
        from movensense.physio.quality import ecg_signal_quality

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

            ch_stats = {"sample_count": len(chunk), "sampling_rate_hz": rate}

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
                            ch_stats["hr_bpm"] = hrv["mean_hr"]
                            ch_stats["hrv_sdnn"] = hrv["sdnn"]
                            ch_stats["hrv_rmssd"] = hrv["rmssd"]
                            ch_stats["r_peak_count"] = len(peaks)
                        sqi = ecg_signal_quality(chunk, rate, window_s=min(5.0, len(chunk) / rate))
                        if sqi:
                            ch_stats["sqi"] = sqi[0]["sqi"]
                            ch_stats["sqi_level"] = sqi[0]["level"]
                    except Exception:
                        pass

            # Activity classification for ACC
            if chunk.ndim == 2 and chunk.shape[1] >= 3:
                try:
                    from movensense.physio.motion import classify_activity
                    labels = classify_activity(chunk, rate)
                    if len(labels) > 0:
                        activity_pct = round(100 * sum(1 for l in labels if l == "activity") / len(labels), 1)
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

    # --- WebSocket Streaming ---

    stream_manager = StreamManager()
    app.state.stream_manager = stream_manager

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
                    elif msg_type == "stop":
                        await stream_manager.stop()
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

    @app.get("/", response_class=HTMLResponse)
    async def root():
        index = STATIC_DIR / "index.html"
        if index.exists():
            return index.read_text()
        return "<h1>Movensense Data Server</h1><p>No UI installed.</p>"

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
