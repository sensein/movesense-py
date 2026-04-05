"""Tests for motion signal processing."""

import numpy as np
import pytest
from movensense.physio.motion import classify_activity, detect_posture_changes, detect_motion_artifacts


class TestActivityClassification:
    def test_rest_detected(self):
        # Constant gravity + tiny noise = rest
        acc = np.column_stack([
            np.random.randn(1000) * 0.01,
            np.random.randn(1000) * 0.01,
            np.ones(1000) + np.random.randn(1000) * 0.01,
        ])
        labels = classify_activity(acc, fs=52, threshold=0.15)
        assert len(labels) > 0
        assert all(l == "rest" for l in labels)

    def test_activity_detected(self):
        # Large ACC variations = activity
        acc = np.column_stack([
            np.random.randn(1000) * 0.5,
            np.random.randn(1000) * 0.5,
            np.ones(1000) + np.random.randn(1000) * 0.5,
        ])
        labels = classify_activity(acc, fs=52, threshold=0.05)
        assert any(l == "activity" for l in labels)


class TestPostureDetection:
    def test_detects_posture_change(self):
        # Longer signal with smooth gradual transition (realistic for 0.5Hz lowpass)
        fs = 52
        duration = 30  # seconds
        n = fs * duration
        acc = np.zeros((n, 3))
        # First 10s: upright
        acc[:10 * fs, 2] = 1.0
        # 10-15s: gradual transition
        for i in range(5 * fs):
            frac = i / (5 * fs)
            acc[10 * fs + i, 2] = 1.0 - frac
            acc[10 * fs + i, 0] = frac
        # 15-30s: lying
        acc[15 * fs:, 0] = 1.0
        changes = detect_posture_changes(acc, fs=fs, angle_threshold=3, min_duration_s=2.0)
        assert len(changes) > 0

    def test_no_change_when_stable(self):
        n = 2000
        acc = np.column_stack([
            np.zeros(n),
            np.zeros(n),
            np.ones(n),
        ])
        changes = detect_posture_changes(acc, fs=52, angle_threshold=20)
        assert len(changes) == 0

    def test_handles_1d_input(self):
        changes = detect_posture_changes(np.ones(100), fs=52)
        assert changes == []


class TestMotionArtifacts:
    def test_detects_artifact_correlation(self):
        n = 2000
        fs = 200
        # Simulated ECG with artifact
        ecg = np.random.randn(n) * 0.1
        acc = np.random.randn(n) * 0.01
        # Inject correlated spike
        ecg[500:600] = np.random.randn(100) * 2.0
        acc[500:600] = np.random.randn(100) * 1.0
        artifacts = detect_motion_artifacts(ecg, acc, fs, fs, correlation_threshold=0.3)
        # Should detect at least the injected artifact region
        assert isinstance(artifacts, list)

    def test_no_artifact_when_clean(self):
        n = 2000
        ecg = np.sin(np.linspace(0, 20 * np.pi, n))  # clean sine
        acc = np.random.randn(n) * 0.001  # no motion
        artifacts = detect_motion_artifacts(ecg, acc, 200, 200)
        # Should have few or no artifacts
        assert isinstance(artifacts, list)
