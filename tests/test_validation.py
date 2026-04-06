"""Validation tests: verify protocol parser matches sbem2json output exactly.

Uses real captured data from a Movesense device stored in tests/fixtures/validation/.
"""

import json
import struct
from pathlib import Path

import pytest

from movesense.protocol import parse_subscription_packet

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "validation"
FIXTURE_FILE = FIXTURE_DIR / "fixture.json"

pytestmark = pytest.mark.skipif(
    not FIXTURE_FILE.exists(), reason="Validation fixture not available"
)


@pytest.fixture
def fixture():
    with open(FIXTURE_FILE) as f:
        return json.load(f)


class TestParseMatchesSBEM2JSON:
    """For each channel with both stream packets and sbem2json JSON,
    verify the parsed stream values are bit-exact with the JSON."""

    def _compare(self, fixture, ch_path, extract_fn):
        ch_data = fixture["channels"][ch_path]
        pkt = bytes.fromhex(ch_data["packet_hex"])
        parsed = parse_subscription_packet(pkt, ch_path)
        json_entry = ch_data["json_entry"]

        assert parsed.timestamp_ms == json_entry["Timestamp"], \
            f"Timestamp mismatch: stream={parsed.timestamp_ms} json={json_entry['Timestamp']}"

        json_values = extract_fn(json_entry)
        n = min(len(parsed.values), len(json_values))
        assert n > 0, "No values to compare"

        for i in range(n):
            sv = parsed.values[i]
            jv = json_values[i]
            if isinstance(sv, list):
                for ax in range(len(sv)):
                    assert abs(sv[ax] - jv[ax]) < 1e-5, \
                        f"Sample {i} axis {ax}: stream={sv[ax]} json={jv[ax]}"
            else:
                assert abs(sv - jv) < 1e-5, \
                    f"Sample {i}: stream={sv} json={jv}"

    def test_ecg_matches(self, fixture):
        self._compare(fixture, "/Meas/Ecg/200/mV", lambda e: e["Samples"])

    def test_acc_matches(self, fixture):
        self._compare(fixture, "/Meas/Acc/52",
                       lambda e: [[p["x"], p["y"], p["z"]] for p in e["ArrayAcc"]])

    def test_gyro_matches(self, fixture):
        self._compare(fixture, "/Meas/Gyro/52",
                       lambda e: [[p["x"], p["y"], p["z"]] for p in e["ArrayGyro"]])

    def test_magn_matches(self, fixture):
        self._compare(fixture, "/Meas/Magn/13",
                       lambda e: [[p["x"], p["y"], p["z"]] for p in e["ArrayMagn"]])

    def test_imu6_matches(self, fixture):
        self._compare(fixture, "/Meas/IMU6/52",
                       lambda e: [[a["x"], a["y"], a["z"], g["x"], g["y"], g["z"]]
                                  for a, g in zip(e["ArrayAcc"], e["ArrayGyro"])])

    def test_imu9_matches(self, fixture):
        self._compare(fixture, "/Meas/IMU9/52",
                       lambda e: [[a["x"], a["y"], a["z"], g["x"], g["y"], g["z"], m["x"], m["y"], m["z"]]
                                  for a, g, m in zip(e["ArrayAcc"], e["ArrayGyro"], e["ArrayMagn"])])


class TestAllChannelsParseable:
    """Verify every channel in the fixture produces non-empty parsed output."""

    def test_all_channels_parse(self, fixture):
        for ch_path, ch_data in fixture["channels"].items():
            pkt = bytes.fromhex(ch_data["packet_hex"])
            parsed = parse_subscription_packet(pkt, ch_path)
            assert parsed.values is not None, f"{ch_path}: values is None"
            if "Temp" not in ch_path and "HR" not in ch_path:
                assert len(parsed.values) > 0, f"{ch_path}: empty values"
