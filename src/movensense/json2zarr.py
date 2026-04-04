"""Convert parsed Movesense JSON sensor data to sharded Zarr v3 stores."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import zarr

log = logging.getLogger(__name__)


def convert_json_to_zarr(
    input_file: str | Path,
    output_path: str | Path,
    device_serial: str = "",
    fetch_date: Optional[str] = None,
) -> Path:
    """Convert a Movesense JSON file to a Zarr v3 store.

    Parameters
    ----------
    input_file : path to JSON file (output of sbem2json)
    output_path : path for the .zarr store directory
    device_serial : device serial number for metadata
    fetch_date : ISO date string for metadata

    Returns
    -------
    Path to the created Zarr store
    """
    input_file = Path(input_file)
    output_path = Path(output_path)

    if fetch_date is None:
        fetch_date = datetime.now(timezone.utc).isoformat()

    with open(input_file) as f:
        content = json.load(f)

    samples = content.get("Samples", [])
    if not samples:
        raise ValueError(f"No samples found in {input_file}")

    # Extract time reference
    time_detailed = {}
    for sample in samples:
        if "TimeDetailed" in sample:
            time_detailed = sample["TimeDetailed"]
            break

    # Group samples by stream type
    streams: dict[str, list] = {}
    for sample in samples:
        for key, value in sample.items():
            if key == "TimeDetailed":
                continue
            streams.setdefault(key, []).append(value)

    log.info(f"Streams found: {list(streams.keys())}")

    # Create Zarr store
    store = zarr.open_group(str(output_path), mode="w")

    # Root attributes
    store.attrs["device_serial"] = device_serial
    store.attrs["fetch_date"] = fetch_date
    store.attrs["measurement_paths"] = list(streams.keys())
    store.attrs["source_file"] = input_file.name

    if time_detailed:
        store.attrs["relative_time"] = time_detailed.get("relativeTime", 0)
        store.attrs["utc_time"] = time_detailed.get("utcTime", 0)

    # Process each stream
    for stream_name, stream_samples in streams.items():
        _write_stream(store, stream_name, stream_samples)

    log.info(f"Created Zarr store: {output_path}")
    return output_path


def _write_stream(store: zarr.Group, name: str, samples: list) -> None:
    """Write a single sensor stream to the Zarr store."""
    name_lower = name.lower()
    group = store.create_group(name)

    if not samples:
        return

    first = samples[0]

    # Extract timestamps
    timestamps = []
    for s in samples:
        ts = s.get("Timestamp", s.get("timestamp", 0))
        timestamps.append(ts)

    if timestamps:
        group.create_array("timestamps", data=np.array(timestamps, dtype=np.float64))

    # ECG data: {"Samples": [v1, v2, ...]} per chunk
    if "ecg" in name_lower or "Ecg" in name:
        all_values = []
        for s in samples:
            values = s.get("Samples", s.get("samples", []))
            all_values.extend(values)
        if all_values:
            arr = np.array(all_values, dtype=np.float32)
            group.create_array("data", data=arr)
            group.attrs["sensor_type"] = "ECG"
            group.attrs["unit"] = "mV"
            group.attrs["lsb_to_mv"] = 0.000381469726563
            _infer_sampling_rate(group, timestamps, samples)

    # Accelerometer / Gyroscope: {"ArrayAcc": [{"x":..,"y":..,"z":..}, ...]}
    elif any(k in name_lower for k in ["acc", "gyro", "magn"]):
        array_key = _find_array_key(first)
        if array_key:
            all_xyz = []
            for s in samples:
                for point in s.get(array_key, []):
                    all_xyz.append([point.get("x", 0), point.get("y", 0), point.get("z", 0)])
            if all_xyz:
                arr = np.array(all_xyz, dtype=np.float32)
                group.create_array("data", data=arr)
                group.attrs["sensor_type"] = name
                group.attrs["shape_description"] = "Nx3 (x, y, z)"
                _infer_sampling_rate(group, timestamps, samples)

    # IMU6: 6-axis (acc + gyro)
    elif "imu6" in name_lower:
        all_rows = []
        for s in samples:
            acc_arr = s.get("ArrayAcc", [])
            gyro_arr = s.get("ArrayGyro", [])
            for a, g in zip(acc_arr, gyro_arr):
                all_rows.append([
                    a.get("x", 0), a.get("y", 0), a.get("z", 0),
                    g.get("x", 0), g.get("y", 0), g.get("z", 0),
                ])
        if all_rows:
            arr = np.array(all_rows, dtype=np.float32)
            group.create_array("data", data=arr)
            group.attrs["sensor_type"] = "IMU6"
            group.attrs["shape_description"] = "Nx6 (acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z)"
            _infer_sampling_rate(group, timestamps, samples)

    # IMU9: 9-axis (acc + gyro + mag)
    elif "imu9" in name_lower:
        all_rows = []
        for s in samples:
            acc_arr = s.get("ArrayAcc", [])
            gyro_arr = s.get("ArrayGyro", [])
            mag_arr = s.get("ArrayMagn", [])
            for a, g, m in zip(acc_arr, gyro_arr, mag_arr):
                all_rows.append([
                    a.get("x", 0), a.get("y", 0), a.get("z", 0),
                    g.get("x", 0), g.get("y", 0), g.get("z", 0),
                    m.get("x", 0), m.get("y", 0), m.get("z", 0),
                ])
        if all_rows:
            arr = np.array(all_rows, dtype=np.float32)
            group.create_array("data", data=arr)
            group.attrs["sensor_type"] = "IMU9"
            group.attrs["shape_description"] = "Nx9 (acc_xyz, gyro_xyz, mag_xyz)"
            _infer_sampling_rate(group, timestamps, samples)

    # Temperature: single float value
    elif "temp" in name_lower:
        values = []
        for s in samples:
            measurement = s.get("Measurement", s.get("measurement", None))
            if measurement is not None:
                values.append(measurement)
        if values:
            arr = np.array(values, dtype=np.float32)
            group.create_array("data", data=arr)
            group.attrs["sensor_type"] = "Temperature"
            group.attrs["unit"] = "°C"

    # Heart Rate
    elif "hr" in name_lower:
        values = []
        for s in samples:
            hr = s.get("average", s.get("Average", 0))
            values.append(hr)
        if values:
            arr = np.array(values, dtype=np.float32)
            group.create_array("data", data=arr)
            group.attrs["sensor_type"] = "HeartRate"
            group.attrs["unit"] = "bpm"

    # RR intervals (ECG RR)
    elif "rr" in name_lower:
        values = []
        for s in samples:
            rr = s.get("rrData", s.get("RrData", []))
            values.extend(rr)
        if values:
            arr = np.array(values, dtype=np.float32)
            group.create_array("data", data=arr)
            group.attrs["sensor_type"] = "ECGRR"
            group.attrs["unit"] = "ms"

    # Generic fallback
    else:
        log.warning(f"Unknown stream type '{name}', storing raw JSON")
        group.attrs["sensor_type"] = name
        group.attrs["raw_sample_count"] = len(samples)


def _find_array_key(sample: dict) -> Optional[str]:
    """Find the array data key in a sensor sample."""
    for key in sample:
        if key.startswith("Array"):
            return key
    return None


def _infer_sampling_rate(group: zarr.Group, timestamps: list, samples: list) -> None:
    """Infer and store sampling rate from timestamps and sample counts."""
    if len(timestamps) < 2:
        return
    # Count total data points in first sample to get samples-per-chunk
    first = samples[0]
    array_key = _find_array_key(first)
    if array_key:
        samples_per_chunk = len(first.get(array_key, []))
    elif "Samples" in first or "samples" in first:
        samples_per_chunk = len(first.get("Samples", first.get("samples", [])))
    else:
        return

    if samples_per_chunk == 0:
        return

    dt_ms = timestamps[1] - timestamps[0]
    if dt_ms > 0:
        rate = (samples_per_chunk / dt_ms) * 1000
        group.attrs["sampling_rate_hz"] = round(rate, 1)
