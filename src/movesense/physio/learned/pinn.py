"""Physics-Informed Neural Networks for physiological dynamical systems.

Implements:
- PirateNet: Adaptive residual connections that progressively deepen (2402.00326)
- PhysicsGRU: Physics-constrained GRU for biomechanical dynamics (2408.16599)
- ResidualAttention: Attention weighted by physics residuals (2509.20349)

These models encode physical priors (conservation laws, differential equations,
physiological constraints) into the network architecture or loss function.
"""

import math
from typing import Callable, Optional

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


# --- PirateNet: Physics-informed Residual Adaptive Network ---

class AdaptiveResidualBlock(nn.Module):
    """Adaptive residual block from PirateNets (arXiv:2402.00326).

    Key idea: learnable gating parameters (alpha) that start near zero,
    making the network effectively shallow at init and progressively
    deepening during training.
    """

    def __init__(self, d_model: int, activation: str = "tanh"):
        super().__init__()
        _require_torch()
        self.linear1 = nn.Linear(d_model, d_model)
        self.linear2 = nn.Linear(d_model, d_model)
        # Gating parameter — initialized small so block starts as identity
        self.alpha = nn.Parameter(torch.zeros(1) + 0.01)
        self.act = {"tanh": torch.tanh, "gelu": F.gelu, "silu": F.silu}[activation]

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.act(self.linear1(x))
        h = self.linear2(h)
        return x + self.alpha * h  # residual with learnable gate


class PirateNet(nn.Module):
    """Physics-informed Residual Adaptive Network.

    Progressively deepening architecture for learning solutions to
    differential equations governing physiological dynamics.

    Can be used to:
    - Learn cardiac dynamics (ECG as solution to coupled ODEs)
    - Model biomechanical motion (ACC/GYRO as rigid body dynamics)
    - Discover governing equations from multi-sensor data
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 128,
        output_dim: int = 1,
        n_blocks: int = 8,
        activation: str = "tanh",
    ):
        super().__init__()
        _require_torch()
        self.encoder = nn.Linear(input_dim, hidden_dim)
        self.blocks = nn.ModuleList([
            AdaptiveResidualBlock(hidden_dim, activation) for _ in range(n_blocks)
        ])
        self.decoder = nn.Linear(hidden_dim, output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """(batch, input_dim) → (batch, output_dim)"""
        h = torch.tanh(self.encoder(x))
        for block in self.blocks:
            h = block(h)
        return self.decoder(h)

    def physics_loss(
        self, x: torch.Tensor, residual_fn: Callable,
    ) -> torch.Tensor:
        """Compute physics-informed loss using a PDE/ODE residual function.

        Parameters
        ----------
        x : input points (batch, input_dim) — e.g., (t, spatial_coords)
        residual_fn : function(x, u, du_dx) → residual tensor
            where u = self(x) and du_dx = autograd derivatives

        Returns
        -------
        Mean squared residual (physics loss)
        """
        x.requires_grad_(True)
        u = self.forward(x)

        # Compute gradients
        du_dx = torch.autograd.grad(
            u, x, grad_outputs=torch.ones_like(u),
            create_graph=True, retain_graph=True,
        )[0]

        residual = residual_fn(x, u, du_dx)
        return torch.mean(residual ** 2)


# --- Physics-Informed GRU ---

class PhysicsGRU(nn.Module):
    """GRU with physics-informed constraints for physiological time series.

    Inspired by sEMG-driven PI-GRU (arXiv:2408.16599).
    Incorporates physical constraints as:
    1. Conservation penalties in the loss function
    2. Bounded output via activation (physiologically plausible ranges)
    3. Smooth dynamics prior (temporal derivative penalty)
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        output_dim: int = 1,
        n_layers: int = 2,
        bidirectional: bool = True,
        output_bounds: Optional[tuple[float, float]] = None,
    ):
        super().__init__()
        _require_torch()
        self.gru = nn.GRU(
            input_dim, hidden_dim, n_layers,
            batch_first=True, bidirectional=bidirectional,
        )
        mult = 2 if bidirectional else 1
        self.proj = nn.Linear(hidden_dim * mult, output_dim)
        self.output_bounds = output_bounds

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """(batch, seq_len, input_dim) → (batch, seq_len, output_dim)"""
        h, _ = self.gru(x)
        out = self.proj(h)
        if self.output_bounds is not None:
            lo, hi = self.output_bounds
            out = lo + (hi - lo) * torch.sigmoid(out)
        return out

    def smoothness_loss(self, predictions: torch.Tensor) -> torch.Tensor:
        """Penalize non-smooth dynamics (temporal derivative regularization)."""
        dt = predictions[:, 1:, :] - predictions[:, :-1, :]
        return torch.mean(dt ** 2)

    def conservation_loss(
        self, predictions: torch.Tensor, conservation_fn: Callable,
    ) -> torch.Tensor:
        """Penalize violations of a conservation law.

        conservation_fn: maps predictions → scalar that should be constant over time.
        """
        conserved = conservation_fn(predictions)  # (batch, seq_len)
        return torch.var(conserved, dim=1).mean()


# --- Residual-Based Attention ---

class ResidualAttention(nn.Module):
    """Attention mechanism weighted by physics residuals.

    Inspired by RBA (arXiv:2509.20349). Regions where the physics residual
    is large get more attention — focusing model capacity on areas where
    the physical model is least accurate.
    """

    def __init__(self, d_model: int, n_heads: int = 4, dropout: float = 0.1):
        super().__init__()
        _require_torch()
        self.attn = nn.MultiheadAttention(d_model, n_heads, dropout=dropout, batch_first=True)
        self.residual_proj = nn.Linear(1, n_heads)  # project scalar residual to per-head weights
        self.norm = nn.LayerNorm(d_model)
        self.n_heads = n_heads

    def forward(
        self, x: torch.Tensor,
        physics_residual: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Attention with optional physics residual weighting.

        Parameters
        ----------
        x : (batch, seq_len, d_model)
        physics_residual : (batch, seq_len, 1) — magnitude of physics violation per timestep
            If None, falls back to standard attention.
        """
        if physics_residual is not None:
            # Convert residuals to attention bias
            # Higher residual → more attention weight (focus on errors)
            bias_per_head = self.residual_proj(physics_residual)  # (batch, seq_len, n_heads)
            bias_per_head = bias_per_head.permute(0, 2, 1)  # (batch, n_heads, seq_len)
            # Expand to full attention mask shape
            attn_mask = bias_per_head.unsqueeze(-1).expand(-1, -1, -1, x.size(1))
            attn_mask = attn_mask.reshape(-1, x.size(1), x.size(1))  # (batch*n_heads, seq, seq)
            out, _ = self.attn(x, x, x, attn_mask=attn_mask)
        else:
            out, _ = self.attn(x, x, x)

        return self.norm(x + out)
