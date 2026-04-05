"""State-Space Model (SSM) backbone for biosignal sequence modeling.

Inspired by Mamba/S4 architectures (BioMamba, ECGMamba).
Efficient linear-time sequence modeling with selective state spaces.
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
        raise ImportError("PyTorch required: pip install 'movensense[ml]'")


class S4Layer(nn.Module):
    """Simplified S4 (Structured State Space Sequence) layer.

    Implements a diagonal state-space model:
        x'(t) = Ax(t) + Bu(t)
        y(t)  = Cx(t) + Du(t)

    With discretization via zero-order hold for efficient parallel scan.
    """

    def __init__(self, d_model: int, d_state: int = 64, dt_min: float = 0.001, dt_max: float = 0.1):
        super().__init__()
        _require_torch()
        self.d_model = d_model
        self.d_state = d_state

        # Learnable SSM parameters (diagonal form)
        self.A_log = nn.Parameter(torch.randn(d_model, d_state))  # log of diagonal A
        self.B = nn.Parameter(torch.randn(d_model, d_state) * 0.01)
        self.C = nn.Parameter(torch.randn(d_model, d_state) * 0.01)
        self.D = nn.Parameter(torch.ones(d_model))

        # Learnable step size
        log_dt = torch.rand(d_model) * (math.log(dt_max) - math.log(dt_min)) + math.log(dt_min)
        self.log_dt = nn.Parameter(log_dt)

    def forward(self, u: torch.Tensor) -> torch.Tensor:
        """Forward pass: (batch, seq_len, d_model) → (batch, seq_len, d_model)."""
        batch, seq_len, d = u.shape
        dt = self.log_dt.exp()  # (d_model,)
        A = -torch.exp(self.A_log)  # (d_model, d_state), negative for stability

        # Discretize: A_bar = exp(A * dt), B_bar = (A_bar - I) * A^{-1} * B
        dtA = torch.einsum("d,ds->ds", dt, A)
        A_bar = torch.exp(dtA)  # (d_model, d_state)
        B_bar = torch.einsum("d,ds->ds", dt, self.B)  # simplified

        # Parallel scan (sequential for simplicity, can be optimized with associative scan)
        x = torch.zeros(batch, self.d_model, self.d_state, device=u.device, dtype=u.dtype)
        outputs = []
        for t in range(seq_len):
            x = A_bar.unsqueeze(0) * x + B_bar.unsqueeze(0) * u[:, t, :].unsqueeze(-1)
            y = torch.einsum("bds,ds->bd", x, self.C) + self.D * u[:, t, :]
            outputs.append(y)

        return torch.stack(outputs, dim=1)  # (batch, seq_len, d_model)


class BioSSM(nn.Module):
    """Bidirectional State-Space Model for biosignal processing.

    Multi-channel input → embedding → bidirectional SSM layers → output.
    Inspired by BioMamba and ECGMamba architectures.
    """

    def __init__(
        self,
        n_channels: int = 1,
        d_model: int = 64,
        d_state: int = 32,
        n_layers: int = 4,
        n_classes: int = 0,  # 0 = feature extraction only
        dropout: float = 0.1,
    ):
        super().__init__()
        _require_torch()

        self.input_proj = nn.Linear(n_channels, d_model)
        self.norm_in = nn.LayerNorm(d_model)

        # Bidirectional SSM layers
        self.fwd_layers = nn.ModuleList([S4Layer(d_model, d_state) for _ in range(n_layers)])
        self.bwd_layers = nn.ModuleList([S4Layer(d_model, d_state) for _ in range(n_layers)])
        self.norms = nn.ModuleList([nn.LayerNorm(d_model) for _ in range(n_layers)])
        self.dropout = nn.Dropout(dropout)

        # Output projection
        self.merge = nn.Linear(d_model * 2, d_model)
        self.n_classes = n_classes
        if n_classes > 0:
            self.classifier = nn.Linear(d_model, n_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass.

        Parameters
        ----------
        x : (batch, seq_len, n_channels) input signal

        Returns
        -------
        If n_classes > 0: (batch, n_classes) class logits
        Else: (batch, seq_len, d_model) features
        """
        h = self.norm_in(self.input_proj(x))

        for fwd, bwd, norm in zip(self.fwd_layers, self.bwd_layers, self.norms):
            h_fwd = fwd(h)
            h_bwd = bwd(torch.flip(h, [1]))
            h_bwd = torch.flip(h_bwd, [1])
            h_bi = self.merge(torch.cat([h_fwd, h_bwd], dim=-1))
            h = norm(h + self.dropout(h_bi))

        if self.n_classes > 0:
            return self.classifier(h.mean(dim=1))

        return h

    def extract_features(self, x: torch.Tensor) -> torch.Tensor:
        """Extract per-sample features (no classification head)."""
        with torch.no_grad():
            return self.forward(x)
