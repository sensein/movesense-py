"""Shared test fixtures for movesense tests."""

import json
from pathlib import Path

import numpy as np
import pytest
import zarr


@pytest.fixture
def fake_data_dir(tmp_path):
    """Create a fake data directory with Zarr stores matching the real structure."""
    serial = "000000000000"
    date = "2026-04-04"

    session_dir = tmp_path / serial / date
    session_dir.mkdir(parents=True)

    # Create a Zarr store with ECG and ACC data
    zarr_path = session_dir / "Movesense_log_1_000000000000.zarr"
    store = zarr.open_group(str(zarr_path), mode="w")
    store.attrs["device_serial"] = serial
    store.attrs["fetch_date"] = "2026-04-04T19:20:00Z"
    store.attrs["measurement_paths"] = ["MeasECGmV", "MeasAcc"]
    store.attrs["utc_time"] = 1712000000000000
    store.attrs["relative_time"] = 1000

    ecg = store.create_group("MeasECGmV")
    ecg.create_array("data", data=np.random.randn(500).astype(np.float32))
    ecg.create_array("timestamps", data=np.arange(500, dtype=np.float64))
    ecg.attrs["sensor_type"] = "ECG"
    ecg.attrs["sampling_rate_hz"] = 200.0
    ecg.attrs["unit"] = "mV"

    acc = store.create_group("MeasAcc")
    acc.create_array("data", data=np.random.randn(100, 3).astype(np.float32))
    acc.create_array("timestamps", data=np.arange(100, dtype=np.float64))
    acc.attrs["sensor_type"] = "MeasAcc"
    acc.attrs["sampling_rate_hz"] = 52.0
    acc.attrs["shape_description"] = "Nx3 (x, y, z)"

    # Also create dummy CSV/JSON files
    (session_dir / "Movesense_log_1_000000000000.csv").write_text("timestamp,value\n0,0.1\n")
    (session_dir / "Movesense_log_1_000000000000.json").write_text("{}")

    # Second device with a different date
    serial2 = "000000000001"
    session_dir2 = tmp_path / serial2 / "2026-04-03"
    session_dir2.mkdir(parents=True)
    zarr_path2 = session_dir2 / "Movesense_log_1_000000000001.zarr"
    store2 = zarr.open_group(str(zarr_path2), mode="w")
    store2.attrs["device_serial"] = serial2
    store2.attrs["measurement_paths"] = ["MeasTemp"]
    temp = store2.create_group("MeasTemp")
    temp.create_array("data", data=np.array([36.5, 36.6], dtype=np.float32))
    temp.attrs["sensor_type"] = "Temperature"
    temp.attrs["unit"] = "°C"

    return tmp_path


@pytest.fixture
def corrupted_data_dir(tmp_path):
    """Create a data directory with a corrupted Zarr store."""
    serial = "000000000099"
    session_dir = tmp_path / serial / "2026-04-04"
    session_dir.mkdir(parents=True)
    # Create a directory that looks like Zarr but isn't valid
    bad_zarr = session_dir / "Movesense_log_1_000000000099.zarr"
    bad_zarr.mkdir()
    (bad_zarr / "garbage").write_text("not a zarr store")
    return tmp_path
