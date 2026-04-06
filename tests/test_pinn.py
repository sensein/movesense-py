"""Tests for physics-informed neural networks and symbolic models."""

import pytest
import numpy as np


@pytest.fixture
def skip_no_torch():
    pytest.importorskip("torch")


class TestPirateNet:
    def test_forward(self, skip_no_torch):
        import torch
        from movesense.physio.learned.pinn import PirateNet
        model = PirateNet(input_dim=2, hidden_dim=32, output_dim=1, n_blocks=4)
        x = torch.randn(10, 2)
        y = model(x)
        assert y.shape == (10, 1)

    def test_adaptive_residual_starts_near_identity(self, skip_no_torch):
        import torch
        from movesense.physio.learned.pinn import AdaptiveResidualBlock
        block = AdaptiveResidualBlock(32)
        x = torch.randn(5, 32)
        y = block(x)
        # With alpha near 0, output should be close to input
        assert torch.allclose(x, y, atol=0.5)

    def test_physics_loss(self, skip_no_torch):
        import torch
        from movesense.physio.learned.pinn import PirateNet
        model = PirateNet(input_dim=1, hidden_dim=16, output_dim=1, n_blocks=2)
        x = torch.randn(20, 1)
        # Simple ODE residual: du/dx - u = 0 (exponential growth)
        def residual_fn(x, u, du_dx):
            return du_dx - u
        loss = model.physics_loss(x, residual_fn)
        assert loss.item() >= 0
        assert loss.requires_grad


class TestPhysicsGRU:
    def test_forward(self, skip_no_torch):
        import torch
        from movesense.physio.learned.pinn import PhysicsGRU
        model = PhysicsGRU(input_dim=3, hidden_dim=32, output_dim=1)
        x = torch.randn(2, 100, 3)
        y = model(x)
        assert y.shape == (2, 100, 1)

    def test_output_bounds(self, skip_no_torch):
        import torch
        from movesense.physio.learned.pinn import PhysicsGRU
        model = PhysicsGRU(input_dim=1, output_dim=1, output_bounds=(0.0, 1.0))
        x = torch.randn(2, 50, 1) * 10  # large inputs
        y = model(x)
        assert y.min() >= 0.0
        assert y.max() <= 1.0

    def test_smoothness_loss(self, skip_no_torch):
        import torch
        from movesense.physio.learned.pinn import PhysicsGRU
        model = PhysicsGRU(input_dim=1, output_dim=1)
        pred = torch.randn(2, 100, 1)
        loss = model.smoothness_loss(pred)
        assert loss.item() >= 0


class TestResidualAttention:
    def test_without_residual(self, skip_no_torch):
        import torch
        from movesense.physio.learned.pinn import ResidualAttention
        layer = ResidualAttention(d_model=32, n_heads=4)
        x = torch.randn(2, 50, 32)
        out = layer(x)
        assert out.shape == (2, 50, 32)

    def test_with_physics_residual(self, skip_no_torch):
        import torch
        from movesense.physio.learned.pinn import ResidualAttention
        layer = ResidualAttention(d_model=32, n_heads=4)
        x = torch.randn(2, 50, 32)
        residual = torch.rand(2, 50, 1)  # physics violation magnitude
        out = layer(x, physics_residual=residual)
        assert out.shape == (2, 50, 32)


class TestKAN:
    def test_kan_layer_forward(self, skip_no_torch):
        import torch
        from movesense.physio.learned.symbolic import KANLayer
        layer = KANLayer(in_features=3, out_features=2, grid_size=5)
        x = torch.randn(10, 3)
        y = layer(x)
        assert y.shape == (10, 2)

    def test_physics_kan(self, skip_no_torch):
        import torch
        from movesense.physio.learned.symbolic import PhysicsKAN
        model = PhysicsKAN(input_dim=2, hidden_dim=8, output_dim=1, n_layers=2)
        x = torch.randn(10, 2)
        y = model(x)
        assert y.shape == (10, 1)

    def test_symbolic_repr(self, skip_no_torch):
        import torch
        from movesense.physio.learned.symbolic import PhysicsKAN
        model = PhysicsKAN(input_dim=2, hidden_dim=4, output_dim=1, n_layers=2)
        # Train briefly to get non-zero coefficients
        x = torch.randn(50, 2)
        y = torch.sin(x[:, 0:1]) + x[:, 1:2]
        opt = torch.optim.Adam(model.parameters(), lr=0.01)
        for _ in range(10):
            loss = ((model(x) - y) ** 2).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        eqs = model.discover_equations(["t", "x"])
        assert len(eqs) > 0
        assert isinstance(eqs[0], str)
