"""Tests for segmentation and pattern discovery."""

import numpy as np
import pytest


class TestChangepoints:
    def test_detects_mean_shift(self):
        from movensense.physio.segmentation import detect_changepoints
        # Clear mean shift at sample 500
        data = np.concatenate([np.random.randn(500), np.random.randn(500) + 5])
        bkps = detect_changepoints(data, method="pelt", penalty=5)
        assert len(bkps) > 0
        # At least one breakpoint near 500
        assert any(abs(b - 500) < 50 for b in bkps)

    def test_multivariate_changepoints(self):
        from movensense.physio.segmentation import detect_changepoints
        data = np.vstack([
            np.random.randn(300, 3),
            np.random.randn(300, 3) + 3,
        ])
        bkps = detect_changepoints(data, penalty=5)
        assert len(bkps) > 0

    def test_no_changepoints_in_stationary(self):
        from movensense.physio.segmentation import detect_changepoints
        data = np.random.randn(500)
        bkps = detect_changepoints(data, penalty=100)
        assert len(bkps) == 0


class TestMultistreamSegmentation:
    def test_segment_two_streams(self):
        from movensense.physio.segmentation import segment_multistream
        streams = {
            "ecg": np.concatenate([np.random.randn(1000), np.random.randn(1000) + 3]),
            "acc": np.concatenate([np.random.randn(100, 3) * 0.1, np.random.randn(100, 3) * 2]),
        }
        rates = {"ecg": 200, "acc": 20}
        segments = segment_multistream(streams, rates, window_s=2.0, penalty=5)
        assert len(segments) > 0
        assert "start_s" in segments[0]
        assert "end_s" in segments[0]


class TestPatternDiscovery:
    def test_discovers_motif(self):
        from movensense.physio.segmentation import discover_patterns
        # Create a signal with a repeating pattern
        pattern = np.sin(np.linspace(0, 2 * np.pi, 100))
        data = np.random.randn(2000) * 0.1
        data[200:300] = pattern
        data[800:900] = pattern
        data[1400:1500] = pattern
        motifs = discover_patterns(data, fs=100, pattern_length_s=1.0, top_k=3)
        assert len(motifs) > 0
        assert "start_idx" in motifs[0]
        assert "distance" in motifs[0]
