# Movesense Device API Reference

Local reference for the Movesense BLE sensor REST-like API accessed via GSP (GATT SensorData Protocol).

## Protocol

All commands are sent over BLE GATT:
- **Service UUID**: `34802252-7185-4d5d-b431-630e7050e8f0`
- **Write Characteristic**: `34800001-7185-4d5d-b431-630e7050e8f0`
- **Notify Characteristic**: `34800002-7185-4d5d-b431-630e7050e8f0`

GSP command codes:
| Code | Name | Description |
|------|------|-------------|
| 0 | HELLO | Handshake, returns device info |
| 1 | SUBSCRIBE | Subscribe to data stream |
| 2 | UNSUBSCRIBE | Stop subscription |
| 3 | FETCH_LOG | Download SBEM data from flash |
| 4 | GET | Read resource value |
| 5 | CLEAR_LOGBOOK | Erase all logs |
| 6 | PUT_DATALOGGER_CONFIG | Set logging configuration |
| 7 | PUT_SYSTEMMODE | Set system mode (5=reboot) |
| 8 | PUT_UTCTIME | Sync UTC time (microseconds) |
| 9 | PUT_DATALOGGER_STATE | Start (3) / Stop (2) logging |

Response codes: 1=CommandResponse, 2=Data, 3=DataPart2

## Measurement Paths

### ECG
| Path | Rates (Hz) | Notes |
|------|-----------|-------|
| `/Meas/ECG/{Rate}` | 125, 128, 200, 250, 256, 500, 512 | Raw integer LSBs (1 LSB = 0.000381469726563 mV) |
| `/Meas/ECG/{Rate}/mV` | same | Millivolt output (firmware ≥2.3) |
| `/Meas/ECG/Info` | GET | Available rates, array size, filter info |
| `/Meas/ECG/Config` | GET, PUT | Low-pass & high-pass filter settings |

### Accelerometer
| Path | Rates (Hz) | Notes |
|------|-----------|-------|
| `/Meas/Acc/{Rate}` | 13, 26, 52, 104, 208, 416, 833, 1666 | Array of {x, y, z} floats |
| `/Meas/Acc/Info` | GET | Available rates & ranges |
| `/Meas/Acc/Config` | GET, PUT | G-range: 2, 4, 8, 16g |

### Gyroscope
| Path | Rates (Hz) | Notes |
|------|-----------|-------|
| `/Meas/Gyro/{Rate}` | 13, 26, 52, 104, 208, 416, 833, 1666 | Array of {x, y, z} floats (DPS) |
| `/Meas/Gyro/Config` | GET, PUT | Range: 245, 500, 1000, 2000 DPS |

### Magnetometer
| Path | Rates (Hz) | Notes |
|------|-----------|-------|
| `/Meas/Magn/{Rate}` | 13, 26, 52, 104, 208, 416, 833, 1666 | Array of {x, y, z} floats |

### Combined IMU
| Path | Sensors | Notes |
|------|---------|-------|
| `/Meas/IMU6/{Rate}` | Acc + Gyro | 6-axis, same rate options |
| `/Meas/IMU6m/{Rate}` | Acc + Magn | 6-axis |
| `/Meas/IMU9/{Rate}` | Acc + Gyro + Magn | 9-axis |

### Heart Rate
| Path | Notes |
|------|-------|
| `/Meas/HR` | Event-driven. Returns `average` (float bpm) + `rrData` (array of RR intervals in ms) |
| `/Meas/HR/Info` | Range: 200-2000 bpm |

### Temperature
| Path | Notes |
|------|-------|
| `/Meas/Temp` | GET or SUBSCRIBE. Returns Kelvins. Range 233-398 K, accuracy ±1 K |

### Time
| Path | Notes |
|------|-------|
| `/Time` | GET, PUT, SUBSCRIBE. UTC microseconds since epoch |
| `/Time/Detailed` | GET, SUBSCRIBE. Maps UTC to sensor timestamps. **Always include in DataLogger config.** |

## DataLogger API

### States
| Value | Name | Description |
|-------|------|-------------|
| 1 | Unknown | Initial/undefined |
| 2 | Ready | Stopped, ready for config changes and data retrieval |
| 3 | Logging | Actively recording to flash |

### Operations by State

**When Ready (state 2):**
- PUT config, start logging, list logs, fetch data, erase memory, query config

**When Logging (state 3):**
- Stop logging, query state, live SUBSCRIBE to measurement paths
- **Cannot**: change config, fetch logs (409 Conflict), erase memory, list log entries

### Resource Paths
| Path | Methods | Description |
|------|---------|-------------|
| `/Mem/DataLogger/Config` | GET, PUT | Measurement paths to log |
| `/Mem/DataLogger/State` | GET, PUT | Start (3) / Stop (2) |
| `/Mem/Logbook/Entries` | GET, DELETE | List or erase all logs |
| `/Mem/Logbook/IsFull` | GET | Check if memory is full |
| `/Logbook/byId/{LogId}/Descriptors` | GET | SBEM metadata for a log |
| `/Logbook/byId/{LogId}/Data` | GET, SUBSCRIBE | Retrieve log data |

## System APIs

| Path | Methods | Description |
|------|---------|-------------|
| `/Info` | GET | Device and platform info |
| `/Info/App` | GET | Running app, enabled modules |
| `/System/Mode` | GET, PUT | Mode: 1=FullPowerOff, 5=Application, 12=FwUpdate |
| `/System/Energy/Level` | GET | Battery percentage |
| `/System/Memory/Heap` | GET | Heap usage |
| `/Comm/Ble/Addr` | GET | BLE MAC address |
| `/Comm/Ble/Peers` | GET, SUBSCRIBE | Connected devices |

## Constraints

- **No concurrent measurement limit documented** — practical limit is BLE throughput + flash write speed + battery
- **ECG**: Only one frequency at a time
- **IMU**: Acc is always active when Gyro or Magn is in use
- **BLE**: Single peripheral connection max, one bonded device at a time
- **DataLogger**: Cannot log string-type data

### Power Consumption (approximate)
| Configuration | Current |
|---------------|---------|
| Acc only (13-416 Hz) | 46-268 µA |
| Acc + Gyro (13-416 Hz) | 227-617 µA |
| Acc + Magn (13-416 Hz) | 66-466 µA |
| ECG (125-512 Hz) | 138-230 µA |

## SBEM Format

SBEM (Suunto BLE Encoded Message) is the binary format for stored sensor data.

**Conversion tool** (`sbem2json`):
```
sbem2json --sbem2json <file> --output <json>    # SBEM → JSON
sbem2json --json2sbem <file> --output <sbem>    # JSON → SBEM
sbem2json --dump-sbem <file>                    # Dump structure info
sbem2json --descriptors <file>                  # Separate descriptor file
sbem2json --heatshrink                          # Handle compressed input
```

**JSON output structure**:
```json
{
  "Samples": [
    {"TimeDetailed": {"relativeTime": 1000, "utcTime": 1712000000000000}},
    {"MeasEcgmV": {"Timestamp": 0, "Samples": [0.1, 0.2, ...]}},
    {"MeasAcc": {"Timestamp": 0, "ArrayAcc": [{"x": 0.1, "y": 0.2, "z": 9.8}, ...]}},
    {"MeasTemp": {"Timestamp": 0, "Measurement": 309.5}},
    {"MeasHR": {"Timestamp": 0, "average": 72.0, "rrData": [820, 815]}}
  ]
}
```

## References

- Device library: https://bitbucket.org/movesense/movesense-device-lib
- Resources: https://www.movesense.com/resources/
- Python tool (this repo): originally from https://bitbucket.org/movesense/python-datalogger-tool
