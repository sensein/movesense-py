"""Tests for multi-stream analysis pipeline."""

import numpy as np
import pytest
from movesense.physio.pipeline import analyze_session


def _make_streams():
    """Create synthetic multi-stream data."""
    fs_ecg = 200
    fs_acc = 52
    duration = 10  # seconds

    # Synthetic ECG with R-peaks
    t = np.arange(0, duration, 1 / fs_ecg)
    ecg = np.random.randn(len(t)) * 0.05
    for peak_time in np.arange(0.5, duration, 0.8):  # ~75bpm
        idx = int(peak_time * fs_ecg)
        if idx < len(ecg) - 2:
            ecg[idx] = 1.0
            ecg[idx - 1] = -0.2
            ecg[idx + 1] = -0.3

    # Synthetic ACC (mostly rest with some motion)
    acc = np.column_stack([
        np.random.randn(int(duration * fs_acc)) * 0.02,
        np.random.randn(int(duration * fs_acc)) * 0.02,
        np.ones(int(duration * fs_acc)) + np.random.randn(int(duration * fs_acc)) * 0.02,
    ])

    return (
        {"MeasECGmV": ecg, "MeasAcc": acc},
        {"MeasECGmV": fs_ecg, "MeasAcc": fs_acc},
    )


class TestPipeline:
    def test_run_all_detectors(self):
        streams, rates = _make_streams()
        events = analyze_session(streams, rates, detectors=["all"])
        assert len(events) > 0
        types = {e.event_type for e in events}
        assert "r_peak" in types

    def test_run_specific_detector(self):
        streams, rates = _make_streams()
        events = analyze_session(streams, rates, detectors=["r_peak"])
        assert all(e.event_type == "r_peak" for e in events)

    def test_ecg_only(self):
        streams = {"MeasECGmV": np.random.randn(2000) * 0.1}
        rates = {"MeasECGmV": 200}
        events = analyze_session(streams, rates, detectors=["sqi"])
        types = {e.event_type for e in events}
        # Should produce quality events, not crash on missing ACC
        assert isinstance(events, list)

    def test_acc_only(self):
        acc = np.column_stack([np.random.randn(520) * 0.5 for _ in range(3)])
        streams = {"MeasAcc": acc}
        rates = {"MeasAcc": 52}
        events = analyze_session(streams, rates, detectors=["activity"])
        assert len(events) > 0
        assert all(e.event_type in ("activity", "rest") for e in events)

    def test_empty_streams(self):
        events = analyze_session({}, {}, detectors=["all"])
        assert events == []
