"""Movesense BLE subscription protocol parser.

Wire format definitions derived from the Movesense device library:
  movesense-device-lib/MovesenseCoreLib/resources/movesense-api/meas/*.yaml

This module is the single source of truth for parsing BLE subscription
data and /Info responses. All other code should use this module.
"""

import logging
import math
import struct
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# --- Wire Format Definitions ---

@dataclass
class SensorFormat:
    """Defines the binary wire format for a sensor subscription."""
    name: str
    path_prefix: str          # e.g., "/Meas/Ecg", "/Meas/Acc"
    sample_type: str          # "int16", "int32", "float32", "vector3d"
    samples_per_packet: int = 16  # default, overridden by ECGInfo.ArraySize
    has_rate_param: bool = True
    unit: str = ""
    scale_factor: float = 1.0  # multiply raw value to get physical unit
    axes: int = 1              # 1 for scalar, 3 for Vector3D


# Formats from Movesense API YAML definitions
SENSOR_FORMATS = {
    # ECG raw: int32 samples (LSB units)
    "ecg_raw": SensorFormat(
        name="ECG", path_prefix="/Meas/ECG", sample_type="int32",
        samples_per_packet=16, unit="LSB", scale_factor=1.0,
    ),
    # ECG mV: YAML says float but wire sends int16. We handle both.
    "ecg_mv": SensorFormat(
        name="ECG (mV)", path_prefix="/Meas/Ecg", sample_type="int16",
        samples_per_packet=16, unit="mV", scale_factor=0.000381469726563,
    ),
    # Accelerometer: FloatVector3D array
    "acc": SensorFormat(
        name="Accelerometer", path_prefix="/Meas/Acc", sample_type="float32",
        unit="m/s²", axes=3,
    ),
    # Gyroscope: FloatVector3D array
    "gyro": SensorFormat(
        name="Gyroscope", path_prefix="/Meas/Gyro", sample_type="float32",
        unit="dps", axes=3,
    ),
    # Magnetometer: FloatVector3D array
    "magn": SensorFormat(
        name="Magnetometer", path_prefix="/Meas/Magn", sample_type="float32",
        unit="µT", axes=3,
    ),
    # IMU6: Acc + Gyro (2 × FloatVector3D arrays)
    "imu6": SensorFormat(
        name="IMU 6-axis", path_prefix="/Meas/IMU6", sample_type="float32",
        unit="m/s²+dps", axes=6,
    ),
    # IMU6m: Acc + Magn
    "imu6m": SensorFormat(
        name="IMU 6-axis (Mag)", path_prefix="/Meas/IMU6m", sample_type="float32",
        unit="m/s²+µT", axes=6,
    ),
    # IMU9: Acc + Gyro + Magn (3 × FloatVector3D arrays)
    "imu9": SensorFormat(
        name="IMU 9-axis", path_prefix="/Meas/IMU9", sample_type="float32",
        unit="m/s²+dps+µT", axes=9,
    ),
    # Temperature: uint32 Timestamp + float Measurement
    "temp": SensorFormat(
        name="Temperature", path_prefix="/Meas/Temp", sample_type="float32",
        has_rate_param=False, unit="K", axes=1,
    ),
    # Heart Rate: float average + uint16[] rrData
    "hr": SensorFormat(
        name="Heart Rate", path_prefix="/Meas/HR", sample_type="float32",
        has_rate_param=False, unit="bpm", axes=1,
    ),
}


def identify_format(channel_path: str) -> Optional[SensorFormat]:
    """Identify the wire format for a subscription channel path."""
    path_lower = channel_path.lower()

    # ECG mV variant
    if "/meas/ecg" in path_lower and "/mv" in path_lower:
        return SENSOR_FORMATS["ecg_mv"]
    if "/meas/ecg" in path_lower:
        return SENSOR_FORMATS["ecg_raw"]

    # Match by prefix (longest match first)
    candidates = sorted(SENSOR_FORMATS.values(), key=lambda f: -len(f.path_prefix))
    for fmt in candidates:
        if path_lower.startswith(fmt.path_prefix.lower()):
            return fmt

    log.warning(f"Unknown channel format: {channel_path}")
    return None


# --- Subscription Data Parser ---

@dataclass
class ParsedPacket:
    """Result of parsing a BLE subscription data packet."""
    timestamp_ms: int = 0
    values: list = field(default_factory=list)  # flat list for 1D, list-of-lists for multi-axis
    channel: str = ""
    unit: str = ""
    axes: int = 1


def parse_subscription_packet(payload: bytes, channel_path: str) -> ParsedPacket:
    """Parse a BLE subscription data packet using the correct format for the channel.

    Parameters
    ----------
    payload : raw bytes from BLE data notification (after GSP header)
    channel_path : the subscription path (e.g., "/Meas/Ecg/200/mV")

    Returns
    -------
    ParsedPacket with timestamp and parsed values
    """
    if len(payload) < 6:
        return ParsedPacket(channel=channel_path)

    fmt = identify_format(channel_path)
    if fmt is None:
        return _parse_generic(payload, channel_path)

    # Extract timestamp (first 4 bytes, uint32 LE)
    timestamp = struct.unpack_from("<I", payload, 0)[0]
    data = payload[4:]

    result = ParsedPacket(
        timestamp_ms=timestamp,
        channel=channel_path,
        unit=fmt.unit,
        axes=fmt.axes,
    )

    if fmt.sample_type == "int16":
        # ECG mV: int16 samples with scale factor
        values = []
        for i in range(0, len(data) - 1, 2):
            raw = struct.unpack_from("<h", data, i)[0]
            values.append(round(raw * fmt.scale_factor, 6))
        result.values = values

    elif fmt.sample_type == "int32":
        # ECG raw: int32 samples
        values = []
        for i in range(0, len(data) - 3, 4):
            raw = struct.unpack_from("<i", data, i)[0]
            values.append(raw * fmt.scale_factor)
        result.values = values

    elif fmt.axes == 1:
        # Scalar float32 (temp, HR)
        values = []
        for i in range(0, len(data) - 3, 4):
            val = struct.unpack_from("<f", data, i)[0]
            if not (math.isnan(val) or math.isinf(val)):
                values.append(round(val, 6))
        result.values = values

    elif fmt.axes == 3:
        # FloatVector3D array: each sample is 3×float32 = 12 bytes
        values = []
        for i in range(0, len(data) - 11, 12):
            x = struct.unpack_from("<f", data, i)[0]
            y = struct.unpack_from("<f", data, i + 4)[0]
            z = struct.unpack_from("<f", data, i + 8)[0]
            values.append([round(x, 4), round(y, 4), round(z, 4)])
        result.values = values

    elif fmt.axes == 6:
        # Two FloatVector3D arrays interleaved (IMU6)
        # Packet: N×(Acc xyz) then N×(Gyro xyz) — or interleaved
        # From YAML: ArrayAcc + ArrayGyro are separate arrays
        half = len(data) // 2
        acc_data = data[:half]
        gyro_data = data[half:]
        values = []
        n_samples = min(len(acc_data) // 12, len(gyro_data) // 12)
        for s in range(n_samples):
            ax = struct.unpack_from("<f", acc_data, s * 12)[0]
            ay = struct.unpack_from("<f", acc_data, s * 12 + 4)[0]
            az = struct.unpack_from("<f", acc_data, s * 12 + 8)[0]
            gx = struct.unpack_from("<f", gyro_data, s * 12)[0]
            gy = struct.unpack_from("<f", gyro_data, s * 12 + 4)[0]
            gz = struct.unpack_from("<f", gyro_data, s * 12 + 8)[0]
            values.append([round(ax, 4), round(ay, 4), round(az, 4),
                           round(gx, 4), round(gy, 4), round(gz, 4)])
        result.values = values

    elif fmt.axes == 9:
        # Three FloatVector3D arrays (IMU9: Acc + Gyro + Magn)
        third = len(data) // 3
        acc_data = data[:third]
        gyro_data = data[third:2 * third]
        magn_data = data[2 * third:]
        values = []
        n_samples = min(len(acc_data) // 12, len(gyro_data) // 12, len(magn_data) // 12)
        for s in range(n_samples):
            row = []
            for chunk in [acc_data, gyro_data, magn_data]:
                x = struct.unpack_from("<f", chunk, s * 12)[0]
                y = struct.unpack_from("<f", chunk, s * 12 + 4)[0]
                z = struct.unpack_from("<f", chunk, s * 12 + 8)[0]
                row.extend([round(x, 4), round(y, 4), round(z, 4)])
            values.append(row)
        result.values = values

    return result


def _parse_generic(payload: bytes, channel: str) -> ParsedPacket:
    """Fallback parser: try int16, then float32."""
    timestamp = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    data = payload[4:]
    values = []

    # Try int16 first (most common for ECG)
    if len(data) % 2 == 0 and len(data) >= 4:
        for i in range(0, len(data), 2):
            values.append(struct.unpack_from("<h", data, i)[0])
    elif len(data) % 4 == 0:
        for i in range(0, len(data), 4):
            val = struct.unpack_from("<f", data, i)[0]
            if not (math.isnan(val) or math.isinf(val)):
                values.append(round(val, 6))

    return ParsedPacket(timestamp_ms=timestamp, values=values, channel=channel)


# --- Info Response Parser ---

@dataclass
class SensorCapability:
    """Parsed capability info for a sensor type."""
    name: str
    available: bool
    sample_rates: list[int] = field(default_factory=list)
    ranges: list = field(default_factory=list)
    path_template: str = ""
    unit: str = ""
    axes: int = 1


def parse_info_response(sensor_id: str, data: bytes) -> SensorCapability:
    """Parse binary /Info response for a sensor type.

    The binary format follows SBEM struct encoding:
    - Arrays are preceded by a uint8 length byte
    - uint16 values are little-endian
    - uint8, float values as per YAML definition

    Parameters
    ----------
    sensor_id : "ecg", "acc", "gyro", "magn", "imu", "hr", "temp"
    data : raw bytes from GET /Meas/{type}/Info response
    """
    fmt = SENSOR_FORMATS.get(sensor_id) or SENSOR_FORMATS.get(f"{sensor_id}_mv")
    if not fmt:
        return SensorCapability(name=sensor_id, available=False)

    cap = SensorCapability(
        name=fmt.name,
        available=True,
        path_template=fmt.path_prefix,
        unit=fmt.unit,
        axes=fmt.axes,
    )

    if not data or len(data) < 2:
        return cap

    try:
        offset = 0

        if sensor_id in ("ecg", "ecg_mv"):
            # ECGInfo: uint16 CurrentSampleRate, uint8 len + uint16[] AvailableSampleRates,
            #          uint16 ArraySize, uint8 len + uint16[] LowPass, uint8 len + float[] HighPass
            if offset + 2 <= len(data):
                # CurrentSampleRate (optional, may not be present)
                pass

            # Try to find sample rates array
            rates = _extract_uint16_array(data)
            if rates:
                cap.sample_rates = sorted(rates)

        elif sensor_id in ("acc", "gyro", "magn"):
            # AccInfo/GyroInfo: uint8 len + uint16[] SampleRates, uint8 len + uint8/uint16[] Ranges
            rates = _extract_uint16_array(data)
            if rates:
                cap.sample_rates = sorted(rates)

        elif sensor_id == "imu":
            # IMUInfo: uint8 len + uint16[] SampleRates, ranges...
            rates = _extract_uint16_array(data)
            if rates:
                cap.sample_rates = sorted(rates)

    except Exception as e:
        log.warning(f"Failed to parse {sensor_id} Info response: {e}")

    return cap


def _extract_uint16_array(data: bytes) -> list[int]:
    """Extract the first uint16 array from SBEM-encoded binary data.

    SBEM arrays: uint8 length followed by N × element_size bytes.
    """
    for offset in range(len(data) - 2):
        count = data[offset]
        if 1 <= count <= 20:  # reasonable array length
            needed = offset + 1 + count * 2
            if needed <= len(data):
                values = []
                for i in range(count):
                    val = struct.unpack_from("<H", data, offset + 1 + i * 2)[0]
                    values.append(val)
                # Validate: sample rates should be in reasonable range
                if all(1 <= v <= 10000 for v in values):
                    return values
    return []
