"""StreamManager: bridges BLE sensor data to WebSocket clients."""

import asyncio
import json
import logging
from enum import Enum
from typing import Optional

from ..protocol import parse_subscription_packet
from ..sensor import SensorCommand, GSP_RESP_DATA, GSP_RESP_DATA_PART2, DataView

log = logging.getLogger(__name__)


class StreamState(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    STREAMING = "streaming"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class StreamManager:
    """Manages a single BLE device connection and fans out data to WebSocket clients."""

    def __init__(self):
        self.state = StreamState.IDLE
        self.device_serial: Optional[str] = None
        self.active_channels: list[str] = []
        self.clients: set = set()  # set of asyncio.Queue (one per WS client)
        self._sensor: Optional[SensorCommand] = None
        self._stream_task: Optional[asyncio.Task] = None
        self._refs: dict[int, str] = {}  # ref_id → channel path

    async def add_client(self) -> asyncio.Queue:
        """Register a new WebSocket client. Returns a queue to read messages from."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.clients.add(q)
        # Send current status
        await q.put(self._status_message())
        return q

    def remove_client(self, q: asyncio.Queue) -> None:
        """Unregister a WebSocket client."""
        self.clients.discard(q)
        if not self.clients and self.state == StreamState.STREAMING:
            # Last client left — schedule cleanup
            asyncio.get_event_loop().call_soon(lambda: asyncio.ensure_future(self.stop()))

    async def start(self, serial: str, channels: list[str]) -> None:
        """Connect to device and start streaming specified channels."""
        if self.state == StreamState.STREAMING:
            await self._broadcast({"type": "error", "message": "Already streaming. Stop first."})
            return

        self.state = StreamState.CONNECTING
        self.device_serial = serial
        self.active_channels = channels
        await self._broadcast(self._status_message())

        try:
            self._sensor = SensorCommand(serial, set_time=False)
            await self._sensor.__aenter__()

            # Get device info
            status = await self._sensor.get_status()
            battery = await self._sensor.get_battery_level()
            status.update(battery)
            await self._broadcast({
                "type": "device_info",
                "serial": status.get("serial_number", serial),
                "product_name": status.get("product_name", "Unknown"),
                "app_version": status.get("app_version", "Unknown"),
                "battery": status.get("battery_level"),
                "datalogger_state": {1: "Unknown", 2: "Ready", 3: "Logging"}.get(status.get("dlstate", 1), "Unknown"),
            })

            # Subscribe to channels
            self._refs = {}
            for i, path in enumerate(channels):
                ref = 10 + i
                result = await self._sensor.subscribe_to_resource(path, reference=ref)
                if result.get("success"):
                    self._refs[ref] = path
                    log.info(f"Subscribed to {path} (ref={ref})")
                else:
                    log.warning(f"Failed to subscribe to {path}: {result}")
                    await self._broadcast({"type": "error", "message": f"Failed to subscribe to {path}"})

            if not self._refs:
                await self._broadcast({"type": "error", "message": "No channels subscribed successfully"})
                await self._cleanup()
                return

            self.state = StreamState.STREAMING
            await self._broadcast(self._status_message())

            # Start data forwarding task
            self._stream_task = asyncio.create_task(self._forward_data())

        except Exception as e:
            self.state = StreamState.ERROR
            await self._broadcast({"type": "error", "message": str(e)})
            await self._broadcast(self._status_message())
            await self._cleanup()

    async def stop(self) -> None:
        """Stop streaming and disconnect from device."""
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        await self._cleanup()
        self.state = StreamState.IDLE
        self.device_serial = None
        self.active_channels = []
        await self._broadcast(self._status_message())

    async def _forward_data(self) -> None:
        """Read BLE data from data_queue and broadcast to all clients."""
        try:
            while self.state == StreamState.STREAMING:
                try:
                    response = await asyncio.wait_for(
                        self._sensor.data_queue.get(), timeout=5.0
                    )
                    resp_code = response.get("response_code")
                    ref = response.get("reference")
                    channel = self._refs.get(ref)

                    if channel and resp_code in [2, 3]:  # GSP_RESP_DATA, GSP_RESP_DATA_PART2
                        payload = response.get("data_payload", b"")
                        if len(payload) > 0:
                            parsed = parse_subscription_packet(payload, channel)
                            if parsed.values:
                                await self._broadcast({
                                    "type": "data",
                                    "channel": channel,
                                    "timestamp": parsed.timestamp_ms,
                                    "values": parsed.values,
                                    "unit": parsed.unit,
                                })

                except asyncio.TimeoutError:
                    # Check if BLE still connected
                    if self._sensor and not self._sensor.is_connected:
                        self.state = StreamState.DISCONNECTED
                        await self._broadcast({"type": "error", "message": "BLE connection lost"})
                        await self._broadcast(self._status_message())
                        return

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Stream forwarding error: {e}")
            self.state = StreamState.ERROR
            await self._broadcast({"type": "error", "message": f"Stream error: {e}"})

    def _parse_payload(self, payload: bytes, channel: str = "") -> list:
        """Parse BLE subscription data payload.

        Subscription data format (from Movesense GSP protocol):
        - Bytes 0-3: timestamp (uint32, little-endian)
        - Bytes 4+: sensor samples

        ECG: int16 samples (LSB * 0.000381469726563 = mV)
        ACC/GYRO/IMU: int16 triplets (x,y,z) scaled by sensor range
        Temp/HR: float32 or int16 depending on firmware
        """
        import struct

        if len(payload) < 6:
            return []

        # Skip 4-byte timestamp
        data = payload[4:]
        ch_lower = channel.lower()

        # ECG: int16 samples → mV
        if 'ecg' in ch_lower:
            ECG_LSB_TO_MV = 0.000381469726563
            values = []
            for i in range(0, len(data) - 1, 2):
                raw = struct.unpack_from('<h', data, i)[0]
                values.append(round(raw * ECG_LSB_TO_MV, 6))
            return values

        # ACC/GYRO/Magn/IMU: int16 values (may need scaling but raw is fine for display)
        if any(k in ch_lower for k in ['acc', 'gyro', 'magn', 'imu']):
            values = []
            for i in range(0, len(data) - 1, 2):
                raw = struct.unpack_from('<h', data, i)[0]
                values.append(raw)
            return values

        # Default: try float32, fallback to int16
        import math
        values = []
        if len(data) % 4 == 0:
            for i in range(0, len(data), 4):
                val = struct.unpack_from('<f', data, i)[0]
                if math.isnan(val) or math.isinf(val):
                    val = 0.0
                values.append(round(val, 6))
        else:
            for i in range(0, len(data) - 1, 2):
                values.append(struct.unpack_from('<h', data, i)[0])

        return values

    async def _broadcast(self, message: dict) -> None:
        """Send a message to all connected clients."""
        dead_clients = set()
        for q in self.clients:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead_clients.add(q)
                log.warning("Client queue full, dropping client")

        for q in dead_clients:
            self.clients.discard(q)

    async def _cleanup(self) -> None:
        """Clean up BLE connection."""
        if self._sensor:
            # Unsubscribe
            for ref in self._refs:
                try:
                    while not self._sensor.data_queue.empty():
                        await self._sensor.data_queue.get()
                    await self._sensor.unsubscribe_from_resource(ref)
                except Exception:
                    pass
            try:
                await self._sensor.__aexit__(None, None, None)
            except Exception:
                pass
            self._sensor = None
            self._refs = {}

    def _status_message(self) -> dict:
        return {
            "type": "status",
            "state": self.state.value,
            "serial": self.device_serial,
            "channels": self.active_channels,
            "clients": len(self.clients),
        }
