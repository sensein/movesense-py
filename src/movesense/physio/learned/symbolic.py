"""Symbolic and equation-discovery approaches for physiological dynamics.

Implements:
- KAN (Kolmogorov-Arnold Network) layer for interpretable function learning
- Symbolic regression interface for discovering governing equations
- Physics-constrained symbolic models

Inspired by Symbolic-KAN (arXiv:2603.23854) and related works on
discovering interpretable governing equations from sensor data.
"""

import math
from typing import Optional

import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


def _require_torch():
    if not HAS_TORCH:
        raise ImportError("PyTorch required: pip install 'movesense[ml]'")


class KANLayer(nn.Module):
    """Kolmogorov-Arnold Network layer.

    Instead of fixed activations on nodes (like MLPs), KAN uses learnable
    univariate functions on edges. Each edge has a B-spline activation
    that can be extracted as a symbolic expression.

    This enables discovery of governing equations from data.
    """

    def __init__(self, in_features: int, out_features: int, grid_size: int = 5, spline_order: int = 3):
        super().__init__()
        _require_torch()
        self.in_features = in_features
        self.out_features = out_features
        self.grid_size = grid_size
        self.spline_order = spline_order

        # B-spline control points (learnable)
        n_basis = grid_size + spline_order
        self.coeff = nn.Parameter(
            torch.randn(out_features, in_features, n_basis) * 0.1
        )

        # Grid points
        h = 2.0 / grid_size
        grid = torch.linspace(-1 - h * spline_order, 1 + h * spline_order, grid_size + 2 * spline_order + 1)
        self.register_buffer("grid", grid)

        # Residual linear (silu base)
        self.base_weight = nn.Parameter(torch.randn(out_features, in_features) * (1 / math.sqrt(in_features)))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """(batch, in_features) → (batch, out_features)"""
        # Base function (SiLU)
        base = F.silu(x)  # (batch, in)
        base_out = F.linear(base, self.base_weight)  # (batch, out)

        # B-spline activation
        spline_out = self._spline_forward(x)  # (batch, out)

        return base_out + spline_out

    def _spline_forward(self, x: torch.Tensor) -> torch.Tensor:
        """Compute B-spline activations for each edge."""
        batch = x.shape[0]

        # Evaluate B-spline basis functions
        bases = self._b_spline_basis(x)  # (batch, in, n_basis)

        # Weighted sum: for each (out, in) pair, combine basis with coefficients
        # coeff: (out, in, n_basis), bases: (batch, in, n_basis)
        # result: (batch, out)
        result = torch.einsum("bin,oin->bo", bases, self.coeff)
        return result

    def _b_spline_basis(self, x: torch.Tensor) -> torch.Tensor:
        """Evaluate B-spline basis functions at input points."""
        grid = self.grid  # (G,)
        x = x.unsqueeze(-1)  # (batch, in, 1)
        # Cox-de Boor recursion (order 0)
        bases = ((x >= grid[:-1]) & (x < grid[1:])).float()  # (batch, in, G-1)

        for k in range(1, self.spline_order + 1):
            n = bases.shape[-1] - 1
            left = (x - grid[:n]) / (grid[k:k + n] - grid[:n] + 1e-8) * bases[..., :n]
            right = (grid[k + 1:k + 1 + n] - x) / (grid[k + 1:k + 1 + n] - grid[1:1 + n] + 1e-8) * bases[..., 1:n + 1]
            bases = left + right

        return bases

    def get_symbolic_repr(self, input_names: Optional[list[str]] = None) -> list[str]:
        """Extract approximate symbolic representation of learned functions.

        Returns a list of string expressions (one per output).
        This is an approximation — the actual function is a B-spline.
        """
        if input_names is None:
            input_names = [f"x{i}" for i in range(self.in_features)]

        expressions = []
        for j in range(self.out_features):
            terms = []
            for i in range(self.in_features):
                coeff_magnitude = self.coeff[j, i].abs().mean().item()
                base_w = self.base_weight[j, i].item()
                if coeff_magnitude > 0.01 or abs(base_w) > 0.01:
                    terms.append(f"{base_w:.3f}*silu({input_names[i]}) + spline({input_names[i]})")
            expressions.append(" + ".join(terms) if terms else "0")
        return expressions


class PhysicsKAN(nn.Module):
    """Physics-informed KAN for discovering governing equations.

    Stacks KAN layers with physics-constrained loss. The learned B-spline
    functions can be inspected to extract symbolic governing equations.
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 16,
        output_dim: int = 1,
        n_layers: int = 2,
        grid_size: int = 5,
    ):
        super().__init__()
        _require_torch()
        dims = [input_dim] + [hidden_dim] * (n_layers - 1) + [output_dim]
        self.layers = nn.ModuleList([
            KANLayer(dims[i], dims[i + 1], grid_size=grid_size)
            for i in range(len(dims) - 1)
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for layer in self.layers:
            x = layer(x)
        return x

    def discover_equations(self, input_names: Optional[list[str]] = None) -> list[str]:
        """Attempt to extract symbolic equations from the trained network."""
        if input_names is None:
            input_names = [f"x{i}" for i in range(self.layers[0].in_features)]

        # For now, return per-layer symbolic representations
        descriptions = []
        for i, layer in enumerate(self.layers):
            names = input_names if i == 0 else [f"h{i}_{j}" for j in range(layer.in_features)]
            descriptions.append(f"Layer {i}: {layer.get_symbolic_repr(names)}")
        return descriptions
