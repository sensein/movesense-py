"""Tests for StreamManager."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from movensense.server.stream import StreamManager, StreamState


@pytest.fixture
def manager():
    return StreamManager()


class TestStreamManagerState:
    def test_starts_idle(self, manager):
        assert manager.state == StreamState.IDLE

    def test_no_clients_initially(self, manager):
        assert len(manager.clients) == 0

    @pytest.mark.asyncio
    async def test_add_client_returns_queue(self, manager):
        q = await manager.add_client()
        assert isinstance(q, asyncio.Queue)
        assert len(manager.clients) == 1

    @pytest.mark.asyncio
    async def test_add_client_receives_status(self, manager):
        q = await manager.add_client()
        msg = q.get_nowait()
        assert msg["type"] == "status"
        assert msg["state"] == "idle"

    @pytest.mark.asyncio
    async def test_remove_client(self, manager):
        q = await manager.add_client()
        manager.remove_client(q)
        assert len(manager.clients) == 0

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_clients(self, manager):
        q1 = await manager.add_client()
        q2 = await manager.add_client()
        # Clear status messages
        q1.get_nowait()
        q2.get_nowait()

        await manager._broadcast({"type": "test", "value": 42})
        assert q1.get_nowait()["value"] == 42
        assert q2.get_nowait()["value"] == 42

    @pytest.mark.asyncio
    async def test_status_message_format(self, manager):
        msg = manager._status_message()
        assert msg["type"] == "status"
        assert msg["state"] == "idle"
        assert msg["serial"] is None
        assert msg["channels"] == []
        assert msg["clients"] == 0

    @pytest.mark.asyncio
    async def test_stop_when_idle(self, manager):
        await manager.stop()
        assert manager.state == StreamState.IDLE
