"""Tests for WebSocket endpoint."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from movesense.server.app import create_app
from movesense.server.auth import set_active_token


@pytest.fixture
def client(fake_data_dir):
    with patch("movesense.server.auth.get_or_create_token", return_value="testtoken123"):
        app = create_app(fake_data_dir)
    set_active_token("testtoken123")
    return TestClient(app)


class TestWebSocketAuth:
    def test_connects_with_valid_token(self, client):
        with client.websocket_connect("/ws/stream?token=testtoken123") as ws:
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "status"
            assert msg["state"] == "idle"

    def test_rejects_invalid_token(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/stream?token=wrongtoken") as ws:
                ws.receive_text()

    def test_rejects_missing_token(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/stream") as ws:
                ws.receive_text()


class TestWebSocketMessaging:
    def test_receives_status_on_connect(self, client):
        with client.websocket_connect("/ws/stream?token=testtoken123") as ws:
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "status"
            assert msg["state"] == "idle"
            assert msg["clients"] >= 1

    def test_stop_when_idle(self, client):
        with client.websocket_connect("/ws/stream?token=testtoken123") as ws:
            ws.receive_text()  # initial status
            ws.send_text(json.dumps({"type": "stop"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "status"
            assert msg["state"] == "idle"
