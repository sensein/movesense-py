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
    output_path: str | Path | None,
    device_serial: str = "",
    fetch_date: Optional[str] = None,
    session_group: Optional[zarr.Group] = None,
    source_blob_hash: str = "",
) -> Path | None:
    """Convert a Movesense JSON file to a Zarr v3 store or session group.

    Parameters
    ----------
    input_file : path to JSON file (output of sbem2json)
    output_path : path for standalone .zarr store (ignored if session_group provided)
    device_serial : device serial number for metadata
    fetch_date : ISO date string for metadata
    session_group : if provided, write into this existing Zarr group (DeviceStore mode)
    source_blob_hash : SHA-256 hash of source SBEM blob (for provenance tracking)

    Returns
    -------
    Path to created standalone store, or None if writing to session_group
    """
    input_file = Path(input_file)

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

    # Determine target group: session group within DeviceStore, or standalone store
    if session_group is not None:
        store = session_group
    else:
        output_path = Path(output_path)
        store = zarr.open_group(str(output_path), mode="w")

    # Metadata
    store.attrs["device_serial"] = device_serial
    store.attrs["fetch_date"] = fetch_date
    store.attrs["measurement_paths"] = list(streams.keys())
    store.attrs["source_file"] = input_file.name
    if source_blob_hash:
        store.attrs["source_blob_hash"] = source_blob_hash

    # Timestamp mapping: normalize relativeTime to µs
    if time_detailed:
        rel_time = time_detailed.get("relativeTime", 0)
        utc_time = time_detailed.get("utcTime", 0)
        # relativeTime is in ms from device; normalize to µs
        store.attrs["timestamp_mapping"] = {
            "relative_time_us": rel_time * 1000,  # ms → µs
            "utc_time_us": utc_time,  # already in µs
        }
        # Legacy attrs for backward compat
        store.attrs["relative_time"] = rel_time
        store.attrs["utc_time"] = utc_time

    # Process each stream
    channel_meta = {}
    for stream_name, stream_samples in streams.items():
        info = _write_stream(store, stream_name, stream_samples, normalize_ts=session_group is not None)
        if info:
            channel_meta[stream_name] = info

    # Store per-channel metadata summary
    if channel_meta:
        store.attrs["channels"] = channel_meta

    if session_group is not None:
        log.info(f"Wrote session group with {len(streams)} channels")
        return None
    else:
        log.info(f"Created Zarr store: {output_path}")
        return Path(output_path)


def _write_stream(store: zarr.Group, name: str, samples: list, normalize_ts: bool = False) -> Optional[dict]:
    """Write a single sensor stream to the Zarr store.

    Returns channel metadata dict (rate_hz, samples, unit, etc.) or None.
    """
    name_lower = name.lower()
    group = store.create_group(name)

    if not samples:
        return None

    first = samples[0]

    # Extract timestamps and optionally normalize to µs
    timestamps = []
    for s in samples:
        ts = s.get("Timestamp", s.get("timestamp", 0))
        timestamps.append(ts)

    if timestamps and normalize_ts:
        # Normalize: device ms → µs
        timestamps = [t * 1000 for t in timestamps]

    if timestamps:
        dtype = np.uint64 if normalize_ts else np.float64
        group.create_array("timestamps", data=np.array(timestamps, dtype=dtype))

    sample_count = 0
    unit = ""
    rate_hz = None

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
            sample_count = len(all_values)
            unit = "mV"
            rate_hz = group.attrs.get("sampling_rate_hz")

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
                sample_count = len(all_xyz)
                rate_hz = group.attrs.get("sampling_rate_hz")

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
            sample_count = len(all_rows)
            rate_hz = group.attrs.get("sampling_rate_hz")

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
            sample_count = len(all_rows)
            rate_hz = group.attrs.get("sampling_rate_hz")

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
            group.attrs["unit"] = "K"
            sample_count = len(values)
            unit = "K"

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
            sample_count = len(values)
            unit = "bpm"

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
            sample_count = len(values)
            unit = "ms"

    # Generic fallback
    else:
        log.warning(f"Unknown stream type '{name}', storing raw JSON")
        group.attrs["sensor_type"] = name
        group.attrs["raw_sample_count"] = len(samples)

    # Return channel metadata for session summary
    if sample_count > 0:
        meta = {"samples": sample_count}
        if rate_hz:
            meta["rate_hz"] = rate_hz
        if unit:
            meta["unit"] = unit
        return meta
    return None


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
