"""Tests for CLI argument parsing and .env loading."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from movensense.cli import cli, _load_env_serial, _resolve_serials


@pytest.fixture
def runner():
    return CliRunner()


class TestEnvLoading:
    def test_load_msn_from_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MSN=000000000000\n")
        with patch("movensense.cli.Path.cwd", return_value=tmp_path):
            result = _load_env_serial()
        assert result == "000000000000"

    def test_load_msn_from_parent_env(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        (parent / ".env").write_text("MSN=999\n")
        with patch("movensense.cli.Path.cwd", return_value=child):
            result = _load_env_serial()
        assert result == "999"

    def test_load_msn_from_environ(self, tmp_path):
        with patch("movensense.cli.Path.cwd", return_value=tmp_path):
            with patch.dict(os.environ, {"MSN": "ENV123"}):
                result = _load_env_serial()
        assert result == "ENV123"

    def test_no_msn_returns_none(self, tmp_path):
        with patch("movensense.cli.Path.cwd", return_value=tmp_path):
            with patch.dict(os.environ, {}, clear=True):
                result = _load_env_serial()
        assert result is None


class TestSerialResolution:
    def test_explicit_serials_used(self):
        assert _resolve_serials(("001", "002")) == ["001", "002"]

    def test_env_fallback(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MSN=ENVSERIAL\n")
        with patch("movensense.cli.Path.cwd", return_value=tmp_path):
            result = _resolve_serials(())
        assert result == ["ENVSERIAL"]

    def test_no_serial_exits(self, tmp_path):
        with patch("movensense.cli.Path.cwd", return_value=tmp_path):
            with patch.dict(os.environ, {}, clear=True):
                with pytest.raises(SystemExit):
                    _resolve_serials(())


class TestCLICommands:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Movesense BLE sensor" in result.output

    def test_status_help(self, runner):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "--serial_numbers" in result.output

    def test_status_dispatches(self, runner):
        with patch("movensense.cli._run") as mock_run:
            mock_run.return_value = {
                "success": True,
                "serial_number": "TEST",
                "product_name": "Movesense",
                "app_version": "1.0.1",
                "battery_level": 95,
                "dlstate": 2,
            }
            result = runner.invoke(cli, ["status", "-s", "123"])
            assert result.exit_code == 0
            assert "Device 123: OK" in result.output
            assert "Battery: 95%" in result.output

    def test_fetch_edf_flag(self, runner):
        with patch("movensense.cli._run") as mock_run:
            mock_run.return_value = {"success": True, "files": [], "output_dir": "/tmp"}
            with patch("movensense.cli._output_dir", return_value=Path("/tmp")):
                result = runner.invoke(cli, ["fetch", "-s", "123", "--edf"])
                assert result.exit_code == 0

    def test_data_dir_default_in_help(self, runner):
        result = runner.invoke(cli, ["fetch", "--help"])
        assert "dbp/data/movesense" in result.output

    def test_erase_force_flag(self, runner):
        with patch("movensense.cli._run") as mock_run:
            mock_run.return_value = {"success": True}
            result = runner.invoke(cli, ["erase", "-s", "123", "--force"])
            assert result.exit_code == 0
            assert "memory erased" in result.output

    def test_erase_no_force_prompts(self, runner):
        with patch("movensense.cli._run") as mock_run:
            mock_run.return_value = {"success": True}
            result = runner.invoke(cli, ["erase", "-s", "123"], input="y\n")
            assert result.exit_code == 0

    def test_config_requires_paths(self, runner):
        result = runner.invoke(cli, ["config", "-s", "123"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "required" in result.output.lower()

    def test_no_command_shows_usage(self, runner):
        result = runner.invoke(cli, [])
        assert "Usage" in result.output or "status" in result.output
