"""Tests for JSON-to-Zarr v3 conversion."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
import zarr

from movesense.json2zarr import convert_json_to_zarr


@pytest.fixture
def sample_ecg_json(tmp_path):
    """Create a sample ECG JSON file matching sbem2json output format."""
    data = {
        "Samples": [
            {"TimeDetailed": {"relativeTime": 1000, "utcTime": 1712000000000000}},
            {"MeasEcgmV": {"Timestamp": 0, "Samples": [0.1, 0.2, 0.3, 0.4, 0.5]}},
            {"MeasEcgmV": {"Timestamp": 25, "Samples": [0.6, 0.7, 0.8, 0.9, 1.0]}},
            {"MeasEcgmV": {"Timestamp": 50, "Samples": [1.1, 1.2, 1.3, 1.4, 1.5]}},
        ]
    }
    path = tmp_path / "ecg_test.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def sample_acc_json(tmp_path):
    """Create a sample accelerometer JSON file."""
    data = {
        "Samples": [
            {"TimeDetailed": {"relativeTime": 500, "utcTime": 1712000000000000}},
            {"MeasAcc": {
                "Timestamp": 0,
                "ArrayAcc": [
                    {"x": 0.1, "y": 0.2, "z": 9.8},
                    {"x": 0.15, "y": 0.25, "z": 9.75},
                ],
            }},
            {"MeasAcc": {
                "Timestamp": 77,
                "ArrayAcc": [
                    {"x": 0.12, "y": 0.22, "z": 9.82},
                    {"x": 0.11, "y": 0.21, "z": 9.81},
                ],
            }},
        ]
    }
    path = tmp_path / "acc_test.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def sample_multi_json(tmp_path):
    """Create a JSON with multiple sensor streams."""
    data = {
        "Samples": [
            {"TimeDetailed": {"relativeTime": 0, "utcTime": 1712000000000000}},
            {"MeasEcgmV": {"Timestamp": 0, "Samples": [0.1, 0.2]}},
            {"MeasTemp": {"Timestamp": 0, "Measurement": 36.5}},
            {"MeasHR": {"Timestamp": 0, "average": 72.0}},
            {"MeasEcgmV": {"Timestamp": 10, "Samples": [0.3, 0.4]}},
            {"MeasTemp": {"Timestamp": 1000, "Measurement": 36.6}},
        ]
    }
    path = tmp_path / "multi_test.json"
    path.write_text(json.dumps(data))
    return path


class TestZarrStoreCreation:
    def test_ecg_creates_zarr_store(self, sample_ecg_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        result = convert_json_to_zarr(sample_ecg_json, zarr_path, device_serial="TEST001")
        assert result.exists()
        store = zarr.open_group(str(zarr_path), mode="r")
        assert "MeasEcgmV" in store

    def test_ecg_data_array(self, sample_ecg_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_ecg_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        ecg = store["MeasEcgmV"]
        data = ecg["data"][:]
        assert data.dtype == np.float32
        assert len(data) == 15  # 3 chunks x 5 samples

    def test_ecg_attributes(self, sample_ecg_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_ecg_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        ecg = store["MeasEcgmV"]
        assert ecg.attrs["sensor_type"] == "ECG"
        assert ecg.attrs["unit"] == "mV"

    def test_acc_creates_nx3_array(self, sample_acc_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_acc_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        acc = store["MeasAcc"]
        data = acc["data"][:]
        assert data.shape == (4, 3)  # 2 chunks x 2 points, 3 axes
        assert data.dtype == np.float32


class TestZarrMetadata:
    def test_root_attributes(self, sample_ecg_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_ecg_json, zarr_path, device_serial="SER123")
        store = zarr.open_group(str(zarr_path), mode="r")
        assert store.attrs["device_serial"] == "SER123"
        assert "fetch_date" in store.attrs
        assert "measurement_paths" in store.attrs
        assert "MeasEcgmV" in store.attrs["measurement_paths"]

    def test_time_reference(self, sample_ecg_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_ecg_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        assert store.attrs["utc_time"] == 1712000000000000

    def test_sampling_rate_inferred(self, sample_ecg_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_ecg_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        ecg = store["MeasEcgmV"]
        assert "sampling_rate_hz" in ecg.attrs
        assert ecg.attrs["sampling_rate_hz"] == 200.0


class TestMultiStream:
    def test_multiple_sensor_types(self, sample_multi_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_multi_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        assert "MeasEcgmV" in store
        assert "MeasTemp" in store
        assert "MeasHR" in store

    def test_temperature_data(self, sample_multi_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_multi_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        temp = store["MeasTemp"]
        data = temp["data"][:]
        assert len(data) == 2
        assert abs(data[0] - 36.5) < 0.01

    def test_heart_rate_data(self, sample_multi_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_multi_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        hr = store["MeasHR"]
        data = hr["data"][:]
        assert len(data) == 1
        assert abs(data[0] - 72.0) < 0.01


class TestDeviceStoreConversion:
    """Test converting JSON into a session group within a DeviceStore."""

    def test_convert_to_session_group(self, sample_ecg_json, tmp_path):
        from movesense.storage import DeviceStore
        device_dir = tmp_path / "device"
        device_dir.mkdir()
        ds = DeviceStore(device_dir)
        ds.open()
        group = ds.add_session(0)
        convert_json_to_zarr(sample_ecg_json, None, device_serial="TEST001", session_group=group)
        assert "MeasEcgmV" in ds.root["0"]
        ecg = ds.root["0"]["MeasEcgmV"]
        assert ecg["data"][:].shape == (15,)

    def test_session_group_has_timestamp_mapping(self, sample_ecg_json, tmp_path):
        from movesense.storage import DeviceStore
        device_dir = tmp_path / "device"
        device_dir.mkdir()
        ds = DeviceStore(device_dir)
        ds.open()
        group = ds.add_session(0)
        convert_json_to_zarr(sample_ecg_json, None, device_serial="TEST001", session_group=group)
        attrs = dict(ds.root["0"].attrs)
        assert "timestamp_mapping" in attrs
        mapping = attrs["timestamp_mapping"]
        assert "relative_time_us" in mapping
        assert "utc_time_us" in mapping

    def test_session_group_has_channel_metadata(self, sample_multi_json, tmp_path):
        from movesense.storage import DeviceStore
        device_dir = tmp_path / "device"
        device_dir.mkdir()
        ds = DeviceStore(device_dir)
        ds.open()
        group = ds.add_session(0)
        convert_json_to_zarr(sample_multi_json, None, device_serial="TEST001", session_group=group)
        attrs = dict(ds.root["0"].attrs)
        assert "channels" in attrs
        assert "MeasEcgmV" in attrs["channels"]
        assert "MeasTemp" in attrs["channels"]

    def test_timestamps_normalized_to_us(self, sample_ecg_json, tmp_path):
        from movesense.storage import DeviceStore
        device_dir = tmp_path / "device"
        device_dir.mkdir()
        ds = DeviceStore(device_dir)
        ds.open()
        group = ds.add_session(0)
        convert_json_to_zarr(sample_ecg_json, None, device_serial="TEST001", session_group=group)
        ts = ds.root["0"]["MeasEcgmV"]["timestamps"][:]
        # Original timestamps were 0, 25, 50 (ms) → should be 0, 25000, 50000 (µs)
        assert ts[0] == 0
        assert ts[1] == 25000
        assert ts[2] == 50000

    def test_two_sessions_different_channels(self, sample_ecg_json, sample_multi_json, tmp_path):
        from movesense.storage import DeviceStore
        device_dir = tmp_path / "device"
        device_dir.mkdir()
        ds = DeviceStore(device_dir)
        ds.open()

        g0 = ds.add_session(0)
        convert_json_to_zarr(sample_ecg_json, None, device_serial="TEST001", session_group=g0)

        g1 = ds.add_session(1)
        convert_json_to_zarr(sample_multi_json, None, device_serial="TEST001", session_group=g1)

        # Session 0: only ECG
        assert "MeasEcgmV" in ds.root["0"]
        assert "MeasTemp" not in ds.root["0"]
        # Session 1: ECG + Temp + HR
        assert "MeasEcgmV" in ds.root["1"]
        assert "MeasTemp" in ds.root["1"]
        assert "MeasHR" in ds.root["1"]


class TestEdgeCases:
    def test_empty_samples_raises(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"Samples": []}))
        zarr_path = tmp_path / "output.zarr"
        with pytest.raises(ValueError, match="No samples"):
            convert_json_to_zarr(path, zarr_path)

    def test_source_file_in_attrs(self, sample_ecg_json, tmp_path):
        zarr_path = tmp_path / "output.zarr"
        convert_json_to_zarr(sample_ecg_json, zarr_path)
        store = zarr.open_group(str(zarr_path), mode="r")
        assert store.attrs["source_file"] == "ecg_test.json"
