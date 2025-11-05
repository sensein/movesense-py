# Python Datalogger Tool

A Python command-line interface (CLI) tool for managing BLE (Bluetooth Low Energy) datalogger devices using the GSP (GATT SensorData Protocol). This tool allows you to configure, control, and retrieve data from compatible sensor devices.

# ECG GUI tool

A graphical user interface (GUI) application for recording and retrieving ECG data from Movesense sensors. This tool simplifies the process of logging ECG sensor data, dowloading it, and converting it to multiple formats for analysis.

## Features

- **Device Status**: Check connection and get device information
- **Configuration**: Set up logging paths and parameters (Only Python Datalogger tool. ECG GUI tool is configured for ECG recordings at 200 Hz with mV units.)
- **Logging Control**: Start and stop data logging
- **Data Retrieval**: Fetch logged data files from devices
- **Memory Management**: Clear device memory when needed
- **Multi-device Support**: Handle multiple sensors simultaneously (Only Python Datalogger tool)
- **Cross-platform**: Works on macOS, Windows, and Linux

## Requirements

- Python 3.7+
- Bluetooth Low Energy (BLE) capable device
- Compatible Movesense sensor(s)

## Installation

1. Clone this repository:
```bash
git clone <repository-url>
cd python-datalogger-tool
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage : Datalogger tool

This command line tool provides several commands to interact with datalogger devices. All commands require specifying device serial numbers (or their last few digits) using the `-s` flag.

## Usage : ECG GUI tool

This graphical user interface tool provides a simplified workflow for ECG data logging. All operations require entering the device serial number in the GUI.

### ECG GUI Quick start
   Follow the numbered steps (1-5) in the interface:
      - **Step 1:** Enter your sensor's serial number
      - **Step 2:** Connect to verify device status
      - **Step 3:** Start logging
      - **Step 4:** Stop logging when done
      - **Step 5:** Load data (downloads and converts automatically)
   Click "Erase Memory" to clear all logged data from sensor

   **Note:** This GUI version is specifically configured for ECG recordings at 200 Hz with mV units.

### Basic Command Structure

```bash
python datalogger_tool.py [command] -s [serial_numbers] [options]
```

### Available Commands

#### 1. Check Device Status

Check if devices are connected and get basic information:

```bash
python datalogger_tool.py status -s 000455 000456
```

Output includes:
- Protocol version
- Serial number
- Product and app information
- Current datalogger state (Ready/Logging/Unknown)

#### 2. Configure Device

Set up logging configuration with resource paths:

```bash
python datalogger_tool.py config -s 000455 -p "/Meas/Temp" "/Meas/ECG/125/mV"
```

- `-p, --path`: Resource paths to add to configuration. separate multiple paths with space.
- Automatically adds `/Time/Detailed` to configuration

#### 3. Start Logging

Begin data logging on devices:

```bash
python datalogger_tool.py start -s 000455 000456
```

#### 4. Stop Logging

Stop data logging on devices:

```bash
python datalogger_tool.py stop -s 000455 000456
```

#### 5. Fetch Data

Retrieve logged data from devices:

```bash
python datalogger_tool.py fetch -s 000455 -o /path/to/output/directory
```

- `-o, --output`: Output directory for downloaded files
- Files are saved as `log_{serial}_{log_id}.sbem`
- Automatically fetches all available logs from each device

#### 6. Erase Memory

Clear all logged data from device memory:

```bash
python datalogger_tool.py erasemem -s 000455 --force
```

- `--force`: Skip confirmation prompt (use with caution!). If --force is not given, asks for confirmation
- ⚠️ **Warning**: This permanently deletes all logged data

### Global Options

- `-V, --verbose`: Enable verbose logging for debugging
- `-s, --serial_numbers`: List of device serial numbers (or their last few digits)

### Example Workflows

#### Complete Logging Session

```bash
# 1. Check device status
python datalogger_tool.py status -s 000455

# 2. Configure logging paths
python datalogger_tool.py config -s 000455 -p "/Meas/Temp" "/Meas/Acc/13"

# 3. Start logging
python datalogger_tool.py start -s 000455

# 4. (Let device collect data...)

# 5. Stop logging
python datalogger_tool.py stop -s 000455

# 6. Fetch data
python datalogger_tool.py fetch -s 000455 -o ./data

# 7. Optional: Clear memory for next session
python datalogger_tool.py erasemem -s 000455 --force
```

#### Multiple Device Management

```bash
# Configure multiple devices at once
python datalogger_tool.py config -s 000455 000456 000457 -p "/Meas/Temp"

# Start logging on all devices
python datalogger_tool.py start -s 000455 000456 000457

# Fetch data from all devices
python datalogger_tool.py fetch -s 000455 000456 000457 -o ./data
```

## Device Discovery

The tool connects to devices using the last part of their serial number. Devices must be:
- Powered on and in range
- Advertising the GSP service UUID: `34802252-7185-4d5d-b431-630e7050e8f0`
- Not connected to other applications

## Error Handling

The tool includes automatic retry logic:
- **Status command**: No retries (quick check)
- **Other commands**: Up to 10 retry attempts with 5-second delays
- Failed devices are automatically retried until success or max attempts reached

## Troubleshooting

### Common Issues

1. **"No devices found"**
   - Ensure devices are powered on and in Bluetooth range
   - Check that devices are not connected to other applications
   - Verify serial numbers are correct

2. **Connection timeouts**
   - Move closer to devices
   - Ensure Bluetooth is enabled on your computer
   - Try restarting Bluetooth service

3. **Permission errors on macOS/Linux**
   - May need to run with `sudo` for Bluetooth access
   - Or add your user to appropriate Bluetooth groups

### Verbose Logging

Enable detailed logging for debugging:

```bash
python datalogger_tool.py -V status -s 000455
```

## Development

### Running Tests

```bash
python -m pytest test_datalogger_tool.py -v
```

### Project Structure

```
├── datalogger_tool.py      # Main CLI interface
├── sensor_command.py       # BLE communication and GSP protocol
├── test_datalogger_tool.py # Unit tests with mocked BLE
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

## Protocol Details

This tool implements the GSP (GATT SensorData Protocol) with the following characteristics:
- **Service UUID**: `34802252-7185-4d5d-b431-630e7050e8f0`
- **Write Characteristic**: `34800001-7185-4d5d-b431-630e7050e8f0`
- **Notify Characteristic**: `34800002-7185-4d5d-b431-630e7050e8f0`

## License

[Add your license information here]

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## Support

For issues and questions:
- Check the troubleshooting section above
- Review existing issues in the repository
- Create a new issue with detailed information about your problem