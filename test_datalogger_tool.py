#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for datalogger_tool.py using mock BLE sensor with serial 000455.
Tests all commands according to GSP (GATT SensorData Protocol) specification.
"""

import unittest
import asyncio
import sys
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from argparse import Namespace
import tempfile

# Add the current directory to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datalogger_tool import (
    fetch_status, configure_device, start_logging, stop_logging,
    fetch_data, erase_memory, status_command, config_command,
    start_command, stop_command, fetch_command, erasemem_command
)
from sensor_command import SensorCommand, CommandType, DataView, GSP_RESP_COMMAND_RESPONSE, GSP_RESP_DATA, GSP_RESP_DATA_PART2


class MockBleakDevice:
    """Mock BLE device for testing."""
    def __init__(self, name: str, address: str):
        self.name = name
        self.address = address


class MockBleakClient:
    """Mock BLE client for testing."""
    def __init__(self, address, disconnected_callback=None):
        self.address = address
        self.disconnected_callback = disconnected_callback
        self.is_connected = False
        self._notifications = {}
        self._sensor = None  # Reference to SensorCommand instance

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    async def start_notify(self, uuid, handler):
        self._notifications[uuid] = handler

    async def stop_notify(self, uuid):
        if uuid in self._notifications:
            del self._notifications[uuid]

    def set_sensor(self, sensor):
        """Set reference to SensorCommand instance for direct queue access."""
        self._sensor = sensor

    async def write_gatt_char(self, uuid, data, response=False):
        # Simulate GSP protocol responses based on command
        if len(data) >= 2:
            command_type = data[0]
            reference = data[1]
            # Trigger a mock GSP response
            await self._trigger_mock_gsp_response(command_type, reference)

    async def _trigger_mock_gsp_response(self, command_type, reference):
        """Simulate GSP protocol responses based on command type."""
        # Use direct queue access if available, otherwise use handler
        if self._sensor and hasattr(self._sensor, 'response_queue'):
            # Direct queue access
            await self._direct_mock_response(command_type, reference)
        elif self._notifications:
            # Fallback to handler
            handler = list(self._notifications.values())[0]
            await self._handler_mock_response(command_type, reference, handler)

    async def _direct_mock_response(self, command_type, reference):
        """Send mock response directly to sensor's response queue."""
        if command_type == 0:  # HELLO
            # GSP HELLO response: protocol_version(1), serial(utf8), product(utf8), dfu_mac(utf8), app_name(utf8), app_version(utf8)
            hello_data = bytearray([1])  # protocol version 1
            hello_data += b"241330000455\x00"  # serial
            hello_data += b"TestSensor\x00"  # product name
            hello_data += b"AA:BB:CC:DD:EE:FF\x00"  # DFU MAC
            hello_data += b"gatt_sensordata_app\x00"  # app name
            hello_data += b"1.0.0\x00"  # app version
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference]) + hello_data

        elif command_type == 6:  # PUT_DATALOGGER_CONFIG
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])

        elif command_type == 9:  # PUT_DATALOGGER_STATE
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])

        elif command_type == 3:  # FETCH_LOG
            # Initial response to FETCH_LOG command
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])
            await self._sensor._notification_handler(None, response)
            # Simulate data packets
            data_packet = bytearray([GSP_RESP_DATA, reference])
            data_packet += (0).to_bytes(4, 'little')  # offset 0
            data_packet += b"test data\x00"  # data
            await self._sensor._notification_handler(None, data_packet)
            # End marker (empty data)
            end_packet = bytearray([GSP_RESP_DATA, reference])
            end_packet += (9).to_bytes(4, 'little')  # offset = data length
            end_packet += b""  # empty data
            await self._sensor._notification_handler(None, end_packet)
            return

        elif command_type == 4:  # GET. only used for GET_DATALOGGER_STATE
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])
            response += bytearray([2])  # state: ready

        elif command_type == 5:  # CLEAR_LOGBOOK
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])

        else:
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 400 & 0xFF, (400 >> 8) & 0xFF])  # Bad request

        await self._sensor._notification_handler(None, response)

    async def _handler_mock_response(self, command_type, reference, handler):
        """Send mock response via notification handler (fallback)."""
        if command_type == 0:  # HELLO
            hello_data = bytearray([1])
            hello_data += b"241330000455\x00"
            hello_data += b"TestSensor\x00"
            hello_data += b"AA:BB:CC:DD:EE:FF\x00"
            hello_data += b"gatt_sensordata_app\x00"
            hello_data += b"1.0.0\x00"
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference]) + hello_data

        elif command_type == 6:  # PUT_DATALOGGER_CONFIG
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])

        elif command_type == 9:  # PUT_DATALOGGER_STATE
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])

        elif command_type == 3:  # FETCH_LOG
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])
            await handler(None, response)
            data_packet = bytearray([GSP_RESP_DATA, reference])
            data_packet += (0).to_bytes(4, 'little')
            data_packet += b"test data\x00"
            await handler(None, data_packet)
            end_packet = bytearray([GSP_RESP_DATA, reference])
            end_packet += (9).to_bytes(4, 'little')
            end_packet += b""
            await handler(None, end_packet)
            return

        elif command_type == 5:  # CLEAR_LOGBOOK
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 200 & 0xFF, (200 >> 8) & 0xFF])

        else:
            response = bytearray([GSP_RESP_COMMAND_RESPONSE, reference, 400 & 0xFF, (400 >> 8) & 0xFF])

        await handler(None, response)


class TestDataloggerCommands(unittest.IsolatedAsyncioTestCase):
    """Test cases for datalogger commands with mock sensor 000455."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_serial = "000455"
        self.mock_device = MockBleakDevice(f"TestSensor_{self.test_serial}", "AA:BB:CC:DD:EE:FF")

        # Create test arguments
        self.args = Namespace()
        self.args.serial_numbers = [self.test_serial]
        self.args.file = None
        self.args.output = None
        self.args.force = False

    @patch('sensor_command.BleakScanner.discover')
    @patch('sensor_command.BleakClient')
    async def test_fetch_status_hello_success(self, mock_client_class, mock_discover):
        """Test successful HELLO command (status fetch)."""
        # Setup mocks
        mock_discover.return_value = [self.mock_device]
        mock_client = MockBleakClient(self.mock_device.address)
        mock_client_class.return_value = mock_client

        # Test status fetch using SensorCommand directly
        async with SensorCommand(self.test_serial) as sensor:
            result = await sensor.get_status()

        # Assertions
        self.assertTrue(result.get('success', False))
        self.assertEqual(result.get('protocol_version'), 1)
        self.assertEqual(result.get('serial_number'), "241330000455")
        self.assertEqual(result.get('product_name'), "TestSensor")
        mock_discover.assert_called_once()

    @patch('sensor_command.BleakScanner.discover')
    @patch('sensor_command.BleakClient')
    async def test_configure_device_success(self, mock_client_class, mock_discover):
        """Test successful PUT_DATALOGGER_CONFIG command."""
        mock_discover.return_value = [self.mock_device]
        mock_client = MockBleakClient(self.mock_device.address)
        mock_client_class.return_value = mock_client

        # Test configure device using SensorCommand directly
        async with SensorCommand(self.test_serial) as sensor:
            result = await sensor.configure_device(bytearray())

        # Assertions
        self.assertTrue(result.get('success', False))
        mock_discover.assert_called_once()

    @patch('sensor_command.BleakScanner.discover')
    @patch('sensor_command.BleakClient')
    async def test_start_logging_success(self, mock_client_class, mock_discover):
        """Test successful PUT_DATALOGGER_STATE start command."""
        mock_discover.return_value = [self.mock_device]
        mock_client = MockBleakClient(self.mock_device.address)
        mock_client_class.return_value = mock_client

        # Test start logging using SensorCommand directly
        async with SensorCommand(self.test_serial) as sensor:
            result = await sensor.start_logging()

        # Assertions
        self.assertTrue(result.get('success', False))
        mock_discover.assert_called_once()

    @patch('sensor_command.BleakScanner.discover')
    @patch('sensor_command.BleakClient')
    async def test_stop_logging_success(self, mock_client_class, mock_discover):
        """Test successful PUT_DATALOGGER_STATE stop command."""
        mock_discover.return_value = [self.mock_device]
        mock_client = MockBleakClient(self.mock_device.address)
        mock_client_class.return_value = mock_client

        # Test stop logging using SensorCommand directly
        async with SensorCommand(self.test_serial) as sensor:
            result = await sensor.stop_logging()

        # Assertions
        self.assertTrue(result.get('success', False))
        mock_discover.assert_called_once()

    @patch('sensor_command.BleakScanner.discover')
    @patch('sensor_command.BleakClient')
    async def test_fetch_data_success(self, mock_client_class, mock_discover):
        """Test successful FETCH_LOG command."""
        mock_discover.return_value = [self.mock_device]
        mock_client = MockBleakClient(self.mock_device.address)
        mock_client_class.return_value = mock_client

        # Test fetch data using SensorCommand directly
        async with SensorCommand(self.test_serial) as sensor:
            result = await sensor.fetch_data(log_id=1)

        # Assertions
        self.assertTrue(result.get('success', False))
        mock_discover.assert_called_once()

    @patch('sensor_command.BleakScanner.discover')
    @patch('sensor_command.BleakClient')
    async def test_erase_memory_success(self, mock_client_class, mock_discover):
        """Test successful CLEAR_LOGBOOK command with force flag."""
        mock_discover.return_value = [self.mock_device]
        mock_client = MockBleakClient(self.mock_device.address)
        mock_client_class.return_value = mock_client

        # Test erase memory using SensorCommand directly
        async with SensorCommand(self.test_serial) as sensor:
            result = await sensor.erase_memory()

        # Assertions
        self.assertTrue(result.get('success', False))
        mock_discover.assert_called_once()

    @patch('sensor_command.BleakScanner.discover')
    async def test_device_not_found(self, mock_discover):
        """Test behavior when device is not found."""
        # Return empty device list
        mock_discover.return_value = []

        # Test device not found using SensorCommand directly
        with self.assertRaises(RuntimeError) as cm:
            async with SensorCommand("NOTFOUND") as sensor:
                pass
        self.assertIn("not found", str(cm.exception))

    @patch('sensor_command.BleakScanner.discover')
    @patch('sensor_command.BleakClient')
    async def test_connection_failure(self, mock_client_class, mock_discover):
        """Test behavior when connection fails."""
        mock_discover.return_value = [self.mock_device]

        # Mock client that fails to connect
        mock_client = Mock()
        mock_client.connect = AsyncMock(side_effect=Exception("Connection failed"))
        mock_client_class.return_value = mock_client

        # Test connection failure using SensorCommand directly
        with self.assertRaises(RuntimeError) as cm:
            async with SensorCommand(self.test_serial) as sensor:
                pass
        self.assertIn("Failed to connect", str(cm.exception))


class TestDataloggerCommandLine(unittest.TestCase):
    """Test the command line interface functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_serial = "000455"
        self.args = Namespace()
        self.args.serial_numbers = [self.test_serial]
        self.args.file = None
        self.args.output = None
        self.args.force = False

    @patch('datalogger_tool.asyncio.run')
    @patch('builtins.print')
    def test_status_command_cli(self, mock_print, mock_asyncio_run):
        """Test status command CLI interface."""
        # Mock successful HELLO result
        mock_asyncio_run.return_value = {
            'success': True,
            'protocol_version': 1,
            'serial_number': '000455',
            'product_name': 'TestSensor',
            'app_name': 'gatt_sensordata_app',
            'app_version': '1.0.0',
            'status_code': 200
        }

        status_command('000455', self.args)

        # Verify print calls
        self.assertTrue(mock_print.called)
        mock_asyncio_run.assert_called()

    @patch('datalogger_tool.asyncio.run')
    @patch('builtins.print')
    def test_config_command_cli(self, mock_print, mock_asyncio_run):
        """Test config command CLI interface."""
        mock_asyncio_run.return_value = {'success': True, 'status_code': 200}

        config_command('000455', self.args)

        self.assertTrue(mock_print.called)
        mock_asyncio_run.assert_called()

    @patch('datalogger_tool.asyncio.run')
    @patch('builtins.print')
    def test_start_command_cli(self, mock_print, mock_asyncio_run):
        """Test start command CLI interface."""
        mock_asyncio_run.return_value = {'success': True, 'status_code': 200}

        start_command('000455', self.args)

        self.assertTrue(mock_print.called)
        mock_asyncio_run.assert_called()

    @patch('datalogger_tool.asyncio.run')
    @patch('builtins.print')
    def test_stop_command_cli(self, mock_print, mock_asyncio_run):
        """Test stop command CLI interface."""
        mock_asyncio_run.return_value = {'success': True, 'status_code': 200}

        stop_command('000455', self.args)

        self.assertTrue(mock_print.called)
        mock_asyncio_run.assert_called()

    @patch('datalogger_tool.asyncio.run')
    @patch('builtins.print')
    def test_fetch_command_cli(self, mock_print, mock_asyncio_run):
        """Test fetch command CLI interface."""
        mock_asyncio_run.return_value = {
            'success': True,
            'filename': 'test_log.sbem'
        }

        fetch_command('000455', self.args)

        self.assertTrue(mock_print.called)
        mock_asyncio_run.assert_called()

    @patch('datalogger_tool.asyncio.run')
    @patch('builtins.print')
    def test_erasemem_command_cli(self, mock_print, mock_asyncio_run):
        """Test erasemem command CLI interface."""
        self.args.force = True
        mock_asyncio_run.return_value = {'success': True, 'status_code': 200}

        erasemem_command('000455', self.args)

        self.assertTrue(mock_print.called)
        mock_asyncio_run.assert_called()

    @patch('datalogger_tool.asyncio.run')
    @patch('builtins.print')
    def test_error_handling_cli(self, mock_print, mock_asyncio_run):
        """Test error handling in CLI interface."""
        mock_asyncio_run.return_value = {
            'success': False,
            'error': 'Device not found'
        }

        status_command('000455', self.args)

        # Verify error message is printed
        self.assertTrue(mock_print.called)
        print_calls = [str(call) for call in mock_print.call_args_list]
        error_printed = any('error' in call.lower() or 'not found' in call.lower() for call in print_calls)
        self.assertTrue(error_printed)


class TestSensorCommandClass(unittest.TestCase):
    """Test the SensorCommand class directly."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_serial = "000455"

    def test_dataview_parsing(self):
        """Test DataView binary parsing functionality."""
        test_data = bytearray([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])
        dv = DataView(test_data)

        # Test uint8
        self.assertEqual(dv.get_uint_8(0), 0x01)
        self.assertEqual(dv.get_uint_8(1), 0x02)

        # Test uint16
        self.assertEqual(dv.get_uint_16(0), 0x0201)  # Little endian

        # Test uint32
        self.assertEqual(dv.get_uint_32(0), 0x04030201)  # Little endian

    @patch('sensor_command.BleakScanner.discover')
    async def test_sensor_command_context_manager(self, mock_discover):
        """Test SensorCommand as context manager."""
        mock_device = MockBleakDevice(f"TestSensor_{self.test_serial}", "AA:BB:CC:DD:EE:FF")
        mock_discover.return_value = [mock_device]

        with patch('sensor_command.BleakClient') as mock_client_class:
            mock_client = MockBleakClient(mock_device.address)
            mock_client_class.return_value = mock_client

            async with SensorCommand(self.test_serial) as sensor:
                self.assertIsNotNone(sensor)
                self.assertEqual(sensor.device_name, mock_device.name)

    def test_command_type_enum(self):
        """Test CommandType enum values match GSP protocol."""
        self.assertEqual(CommandType.HELLO.value, 0)
        self.assertEqual(CommandType.SUBSCRIBE.value, 1)
        self.assertEqual(CommandType.UNSUBSCRIBE.value, 2)
        self.assertEqual(CommandType.FETCH_LOG.value, 3)
        self.assertEqual(CommandType.GET.value, 4)
        self.assertEqual(CommandType.CLEAR_LOGBOOK.value, 5)
        self.assertEqual(CommandType.PUT_DATALOGGER_CONFIG.value, 6)
        self.assertEqual(CommandType.PUT_SYSTEMMODE.value, 7)
        self.assertEqual(CommandType.PUT_UTCTIME.value, 8)
        self.assertEqual(CommandType.PUT_DATALOGGER_STATE.value, 9)


async def run_async_tests():
    """Run any additional async tests not covered by unittest."""
    print("Running additional async tests...")

    # All async tests are now handled by unittest.IsolatedAsyncioTestCase
    # No additional async tests needed
    pass


def main():
    """Main test runner."""
    print("Starting datalogger command tests with mock sensor 000455 (GSP protocol)...\n")

    # Run unittest tests (now includes async tests via IsolatedAsyncioTestCase)
    print("Running all tests...")
    unittest.main(argv=[''], exit=False, verbosity=2)

    print("\nAll tests completed!")


if __name__ == '__main__':
    main()