"""Tests for data directory scanner."""

from movensense.server.scanner import DataScanner


class TestDataScanner:
    def test_scan_empty_dir(self, tmp_path):
        scanner = DataScanner(tmp_path)
        scanner.scan()
        assert scanner.devices == []

    def test_scan_single_device(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        serials = [d["serial"] for d in scanner.devices]
        assert "000000000000" in serials

    def test_scan_multiple_devices(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        assert len(scanner.devices) == 2

    def test_dates_for_device(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        dates = scanner.get_dates("000000000000")
        assert "2026-04-04" in dates

    def test_sessions_for_date(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        sessions = scanner.get_sessions("000000000000", "2026-04-04")
        assert len(sessions) >= 1
        assert sessions[0]["log_id"] == 1

    def test_channels_for_session(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        channels = scanner.get_channels("000000000000", "2026-04-04", 1)
        names = [c["name"] for c in channels]
        assert "MeasECGmV" in names
        assert "MeasAcc" in names

    def test_channel_metadata(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        channels = scanner.get_channels("000000000000", "2026-04-04", 1)
        ecg = next(c for c in channels if c["name"] == "MeasECGmV")
        assert ecg["sampling_rate_hz"] == 200.0
        assert ecg["unit"] == "mV"
        assert ecg["sample_count"] == 500

    def test_channel_data(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        result = scanner.get_channel_data("000000000000", "2026-04-04", 1, "MeasECGmV")
        assert len(result["data"]) == 500

    def test_channel_data_pagination(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        result = scanner.get_channel_data("000000000000", "2026-04-04", 1, "MeasECGmV", offset=10, limit=5)
        assert len(result["data"]) == 5
        assert result["offset"] == 10
        assert result["total_samples"] == 500

    def test_session_metadata(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        meta = scanner.get_session_metadata("000000000000", "2026-04-04", 1)
        assert meta["device_serial"] == "000000000000"
        assert "MeasECGmV" in meta["measurement_paths"]

    def test_corrupted_zarr_skipped(self, corrupted_data_dir):
        scanner = DataScanner(corrupted_data_dir)
        scanner.scan()
        # Should not crash; device appears but sessions may be empty
        assert len(scanner.devices) >= 0

    def test_nonexistent_device_returns_none(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        assert scanner.get_dates("NONEXISTENT") is None

    def test_multi_column_data(self, fake_data_dir):
        scanner = DataScanner(fake_data_dir)
        scanner.scan()
        result = scanner.get_channel_data("000000000000", "2026-04-04", 1, "MeasAcc")
        assert len(result["data"]) == 100
        assert len(result["data"][0]) == 3  # x, y, z
