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
