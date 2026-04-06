"""Tests for Movesense BLE subscription protocol parser."""

import struct
import numpy as np
import pytest

from movesense.protocol import (
    ParsedPacket, SensorCapability,
    identify_format, parse_subscription_packet, parse_info_response,
)


def _make_ecg_packet(timestamp=1000, n_samples=16, values=None):
    """Build a synthetic ECG /mV subscription packet: 4B ts + N×int16."""
    ts = struct.pack("<I", timestamp)
    if values is None:
        values = [int(100 * np.sin(i * 0.5)) for i in range(n_samples)]
    data = b"".join(struct.pack("<h", v) for v in values)
    return ts + data


def _make_acc_packet(timestamp=2000, n_samples=4):
    """Build a synthetic ACC packet: 4B ts + N×FloatVector3D (3×float32)."""
    ts = struct.pack("<I", timestamp)
    data = b""
    for i in range(n_samples):
        data += struct.pack("<fff", 0.1 * i, -0.2 * i, 9.8 + 0.01 * i)
    return ts + data


def _make_imu9_packet(timestamp=3000, n_samples=2):
    """Build IMU9: 4B ts + N×acc_xyz + N×gyro_xyz + N×magn_xyz."""
    ts = struct.pack("<I", timestamp)
    acc = b"".join(struct.pack("<fff", 0.1, 0.2, 9.8) for _ in range(n_samples))
    gyro = b"".join(struct.pack("<fff", 1.0, -1.0, 0.5) for _ in range(n_samples))
    magn = b"".join(struct.pack("<fff", 30.0, -20.0, 45.0) for _ in range(n_samples))
    return ts + acc + gyro + magn


class TestIdentifyFormat:
    def test_ecg_mv(self):
        fmt = identify_format("/Meas/Ecg/200/mV")
        assert fmt is not None
        assert fmt.sample_type == "int16"
        assert fmt.unit == "mV"

    def test_ecg_raw(self):
        fmt = identify_format("/Meas/ECG/200")
        assert fmt is not None
        assert fmt.sample_type == "int32"

    def test_acc(self):
        fmt = identify_format("/Meas/Acc/52")
        assert fmt is not None
        assert fmt.axes == 3
        assert fmt.sample_type == "float32"

    def test_imu9(self):
        fmt = identify_format("/Meas/IMU9/52")
        assert fmt is not None
        assert fmt.axes == 9

    def test_temp(self):
        fmt = identify_format("/Meas/Temp")
        assert fmt is not None
        assert fmt.unit == "K"

    def test_unknown(self):
        assert identify_format("/Unknown/Path") is None


class TestParseECG:
    def test_ecg_mv_packet(self):
        pkt = _make_ecg_packet(timestamp=5000, n_samples=16)
        result = parse_subscription_packet(pkt, "/Meas/Ecg/200/mV")
        assert result.timestamp_ms == 5000
        assert len(result.values) == 16
        assert result.unit == "mV"
        # Values should be in mV range (small numbers)
        assert all(abs(v) < 1.0 for v in result.values)

    def test_ecg_scale_factor(self):
        # ECG /mV: int16 × 0.001 = millivolts (verified against sbem2json)
        pkt = struct.pack("<I", 0) + struct.pack("<h", 1000)
        result = parse_subscription_packet(pkt, "/Meas/Ecg/200/mV")
        assert abs(result.values[0] - 1.0) < 1e-10  # 1000 × 0.001 = 1.0 mV

    def test_ecg_negative_values(self):
        pkt = _make_ecg_packet(values=[-500, -1000, 500, 1000])
        result = parse_subscription_packet(pkt, "/Meas/Ecg/200/mV")
        assert result.values[0] < 0
        assert result.values[2] > 0


class TestParseACC:
    def test_acc_vector3d(self):
        pkt = _make_acc_packet(n_samples=4)
        result = parse_subscription_packet(pkt, "/Meas/Acc/52")
        assert result.timestamp_ms == 2000
        assert len(result.values) == 4
        assert len(result.values[0]) == 3  # x, y, z
        assert result.axes == 3

    def test_acc_gravity(self):
        # At rest, z-axis should be ~9.8 m/s²
        pkt = _make_acc_packet(n_samples=1)
        result = parse_subscription_packet(pkt, "/Meas/Acc/52")
        z = result.values[0][2]
        assert abs(z - 9.8) < 0.1


class TestParseIMU9:
    def test_imu9_nine_axes(self):
        pkt = _make_imu9_packet(n_samples=2)
        result = parse_subscription_packet(pkt, "/Meas/IMU9/52")
        assert len(result.values) == 2
        assert len(result.values[0]) == 9  # acc(3) + gyro(3) + magn(3)

    def test_imu9_values(self):
        pkt = _make_imu9_packet(n_samples=1)
        result = parse_subscription_packet(pkt, "/Meas/IMU9/52")
        row = result.values[0]
        # acc: 0.1, 0.2, 9.8
        assert abs(row[0] - 0.1) < 0.01
        assert abs(row[2] - 9.8) < 0.01
        # gyro: 1.0, -1.0, 0.5
        assert abs(row[3] - 1.0) < 0.01
        # magn: 30.0, -20.0, 45.0
        assert abs(row[6] - 30.0) < 0.1


class TestParseGeneric:
    def test_short_payload(self):
        result = parse_subscription_packet(b"\x00\x01", "/Meas/Unknown")
        assert result.values == []

    def test_temp(self):
        # Temp subscription: float Measurement directly (no timestamp prefix)
        pkt = struct.pack("<f", 310.5) + b"\x00\x00\x00\x00"  # + padding
        result = parse_subscription_packet(pkt, "/Meas/Temp")
        assert len(result.values) == 1
        assert abs(result.values[0] - 310.5) < 0.01

    def test_hr(self):
        # HR subscription: float average + uint16 rr
        pkt = struct.pack("<f", 72.5) + struct.pack("<H", 828)
        result = parse_subscription_packet(pkt, "/Meas/HR")
        assert len(result.values) == 1
        assert abs(result.values[0] - 72.5) < 0.1


class TestInfoParser:
    def test_parse_ecg_info(self):
        # Simulate: uint8 count=7, then 7×uint16 rates
        rates = [125, 128, 200, 250, 256, 500, 512]
        data = bytes([len(rates)]) + b"".join(struct.pack("<H", r) for r in rates)
        cap = parse_info_response("ecg", data)
        assert cap.available
        assert cap.sample_rates == sorted(rates)

    def test_parse_acc_info(self):
        rates = [13, 26, 52, 104, 208]
        data = bytes([len(rates)]) + b"".join(struct.pack("<H", r) for r in rates)
        cap = parse_info_response("acc", data)
        assert cap.sample_rates == sorted(rates)

    def test_empty_data(self):
        cap = parse_info_response("ecg", b"")
        assert cap.available  # format known, just no rates parsed

    def test_unknown_sensor(self):
        cap = parse_info_response("unknown_sensor", b"\x00")
        assert not cap.available
