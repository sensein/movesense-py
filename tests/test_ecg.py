"""Tests for ECG processing algorithms."""

import numpy as np
import pytest
from movensense.physio.ecg import detect_r_peaks, compute_rr_intervals, compute_hrv, compute_heart_rate


def _synthetic_ecg(fs=200, duration=10, hr=72):
    """Generate synthetic ECG-like signal with known R-peak positions."""
    t = np.arange(0, duration, 1 / fs)
    interval = 60 / hr  # seconds between beats
    peak_times = np.arange(0.5, duration, interval)
    ecg = np.random.randn(len(t)) * 0.05  # baseline noise

    # Add QRS-like spikes at known positions
    for pt in peak_times:
        idx = int(pt * fs)
        if idx < len(ecg) - 5:
            ecg[idx] = 1.0     # R-peak
            ecg[idx - 1] = -0.2  # Q
            ecg[idx + 1] = -0.3  # S
            ecg[idx + 2] = 0.1   # T wave start

    return ecg, peak_times, fs


class TestRPeakDetection:
    def test_detects_peaks_in_synthetic_ecg(self):
        ecg, expected_peaks, fs = _synthetic_ecg()
        detected = detect_r_peaks(ecg, fs)
        # Should detect most peaks (>90% sensitivity)
        assert len(detected) > 0.9 * len(expected_peaks)

    def test_peaks_near_expected_positions(self):
        ecg, expected_peaks, fs = _synthetic_ecg()
        detected = detect_r_peaks(ecg, fs)
        detected_times = detected / fs
        # Most detected peaks should be near expected (within 100ms, allowing filter delay)
        matched = 0
        for dt in detected_times:
            distances = np.abs(expected_peaks - dt)
            if np.min(distances) < 0.1:
                matched += 1
        assert matched > 0.6 * len(detected_times)

    def test_simple_threshold_method(self):
        ecg, _, fs = _synthetic_ecg()
        detected = detect_r_peaks(ecg, fs, method="simple_threshold")
        assert len(detected) > 0

    def test_empty_signal(self):
        detected = detect_r_peaks(np.zeros(100), 200)
        assert len(detected) == 0


class TestRRIntervals:
    def test_rr_intervals_from_peaks(self):
        peaks = np.array([0, 200, 400, 600])  # at 200Hz = 1s apart
        rr = compute_rr_intervals(peaks, fs=200)
        np.testing.assert_allclose(rr, [1000, 1000, 1000])  # ms

    def test_heart_rate_from_rr(self):
        rr = np.array([1000, 1000])  # 1s intervals = 60bpm
        hr = compute_heart_rate(rr)
        np.testing.assert_allclose(hr, [60, 60])


class TestHRV:
    def test_hrv_metrics(self):
        # Regular intervals = low variability
        rr = np.array([800, 810, 790, 805, 795, 800, 810, 790])
        hrv = compute_hrv(rr)
        assert hrv["sdnn"] > 0
        assert hrv["rmssd"] > 0
        assert hrv["mean_hr"] > 0
        assert hrv["mean_rr"] > 0

    def test_hrv_with_few_intervals(self):
        hrv = compute_hrv(np.array([800]))
        assert hrv["sdnn"] == 0

    def test_pnn50(self):
        # Alternating short/long intervals
        rr = np.array([700, 800, 700, 800, 700, 800])
        hrv = compute_hrv(rr)
        assert hrv["pnn50"] > 0  # 100ms differences > 50ms threshold
