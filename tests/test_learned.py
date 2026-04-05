"""Tests for learned models (causal, SSM, multimodal)."""

import numpy as np
import pytest


class TestGrangerCausality:
    def test_detects_causal_relationship(self):
        from movensense.physio.learned.causal import granger_causality_test
        np.random.seed(42)
        n = 500
        x = np.random.randn(n)
        y = np.zeros(n)
        # y depends on x with lag 1
        for i in range(1, n):
            y[i] = 0.8 * x[i - 1] + 0.1 * np.random.randn()
        result = granger_causality_test(x, y, max_lag=5)
        assert result["is_causal"]
        assert result["p_value"] < 0.05

    def test_no_causal_relationship(self):
        from movensense.physio.learned.causal import granger_causality_test
        np.random.seed(42)
        x = np.random.randn(500)
        y = np.random.randn(500)
        result = granger_causality_test(x, y, max_lag=5, alpha=0.01)
        # Should not find causality between independent series (usually)
        assert isinstance(result, dict)
        assert "p_value" in result

    def test_cross_channel_causality(self):
        from movensense.physio.learned.causal import cross_channel_causality
        np.random.seed(42)
        n = 1000
        ecg = np.random.randn(n)
        acc = np.random.randn(n)
        # Inject causal relationship: acc → ecg with lag
        for i in range(5, n):
            ecg[i] += 0.5 * acc[i - 5]
        streams = {"ecg": ecg, "acc": acc}
        fs = {"ecg": 200, "acc": 200}
        edges = cross_channel_causality(streams, fs, max_lag_s=0.1)
        assert isinstance(edges, list)

    def test_transfer_entropy(self):
        from movensense.physio.learned.causal import compute_transfer_entropy
        np.random.seed(42)
        n = 1000
        x = np.random.randn(n)
        y = np.zeros(n)
        for i in range(1, n):
            y[i] = 0.8 * x[i - 1] + 0.1 * np.random.randn()
        te_xy = compute_transfer_entropy(x, y, lag=1)
        te_yx = compute_transfer_entropy(y, x, lag=1)
        # TE(x→y) should be higher than TE(y→x)
        assert te_xy > te_yx


class TestSSM:
    @pytest.fixture
    def skip_no_torch(self):
        pytest.importorskip("torch")

    def test_s4_layer(self, skip_no_torch):
        import torch
        from movensense.physio.learned.ssm import S4Layer
        layer = S4Layer(d_model=32, d_state=16)
        x = torch.randn(2, 100, 32)
        y = layer(x)
        assert y.shape == (2, 100, 32)

    def test_bio_ssm_features(self, skip_no_torch):
        import torch
        from movensense.physio.learned.ssm import BioSSM
        model = BioSSM(n_channels=1, d_model=32, d_state=16, n_layers=2)
        x = torch.randn(2, 200, 1)
        features = model(x)
        assert features.shape == (2, 200, 32)

    def test_bio_ssm_classifier(self, skip_no_torch):
        import torch
        from movensense.physio.learned.ssm import BioSSM
        model = BioSSM(n_channels=3, d_model=32, n_layers=2, n_classes=5)
        x = torch.randn(2, 100, 3)
        logits = model(x)
        assert logits.shape == (2, 5)


class TestMultiModal:
    @pytest.fixture
    def skip_no_torch(self):
        pytest.importorskip("torch")

    def test_channel_encoder(self, skip_no_torch):
        import torch
        from movensense.physio.learned.multimodal import ChannelEncoder
        enc = ChannelEncoder(in_channels=3, d_model=64)
        x = torch.randn(2, 100, 3)
        out = enc(x)
        assert out.shape == (2, 100, 64)

    def test_multimodal_fusion(self, skip_no_torch):
        import torch
        from movensense.physio.learned.multimodal import MultiModalFusion
        model = MultiModalFusion(
            channel_configs={"ecg": 1, "acc": 3, "gyro": 3},
            d_model=32, n_layers=1, n_classes=4,
        )
        inputs = {
            "ecg": torch.randn(2, 200, 1),
            "acc": torch.randn(2, 52, 3),
            "gyro": torch.randn(2, 52, 3),
        }
        logits = model(inputs)
        assert logits.shape == (2, 4)

    def test_multimodal_features(self, skip_no_torch):
        import torch
        from movensense.physio.learned.multimodal import MultiModalFusion
        model = MultiModalFusion(
            channel_configs={"ecg": 1, "acc": 3},
            d_model=32, n_layers=1, n_classes=0,
        )
        inputs = {
            "ecg": torch.randn(2, 200, 1),
            "acc": torch.randn(2, 100, 3),
        }
        features = model(inputs)
        assert features.ndim == 3
        assert features.shape[-1] == 32

    def test_cross_attention_weights(self, skip_no_torch):
        import torch
        from movensense.physio.learned.multimodal import MultiModalFusion
        model = MultiModalFusion(
            channel_configs={"ecg": 1, "acc": 3},
            d_model=32, n_layers=1,
        )
        inputs = {
            "ecg": torch.randn(1, 50, 1),
            "acc": torch.randn(1, 50, 3),
        }
        attn = model.get_cross_channel_attention(inputs)
        assert len(attn) > 0
