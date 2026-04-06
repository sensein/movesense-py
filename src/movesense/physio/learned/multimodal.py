"""Multi-modal sensor fusion and cross-channel representation learning.

Models that integrate across different sensor types (ECG, ACC, GYRO, Temp, HR)
to learn joint representations and cross-modal predictions.
"""

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


class ChannelEncoder(nn.Module):
    """Per-channel temporal encoder using 1D convolutions.

    Maps a single sensor channel to a fixed-size representation per time window.
    """

    def __init__(self, in_channels: int = 1, d_model: int = 64, kernel_sizes: list[int] = [3, 7, 15]):
        super().__init__()
        _require_torch()
        n = len(kernel_sizes)
        dims = [d_model // n] * n
        dims[-1] = d_model - sum(dims[:-1])  # absorb integer division remainder
        self.convs = nn.ModuleList([
            nn.Sequential(
                nn.Conv1d(in_channels, bd, k, padding=k // 2),
                nn.BatchNorm1d(bd),
                nn.GELU(),
            )
            for k, bd in zip(kernel_sizes, dims)
        ])
        self.proj = nn.Linear(d_model, d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """(batch, seq_len, in_channels) → (batch, seq_len, d_model)"""
        x = x.transpose(1, 2)  # (batch, channels, seq_len)
        features = [conv(x) for conv in self.convs]
        h = torch.cat(features, dim=1)  # (batch, d_model, seq_len)
        return self.proj(h.transpose(1, 2))  # (batch, seq_len, d_model)


class CrossModalAttention(nn.Module):
    """Cross-attention between different sensor modalities.

    Each modality attends to all others to learn cross-channel dependencies.
    """

    def __init__(self, d_model: int = 64, n_heads: int = 4, dropout: float = 0.1):
        super().__init__()
        _require_torch()
        self.attn = nn.MultiheadAttention(d_model, n_heads, dropout=dropout, batch_first=True)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, query: torch.Tensor, context: torch.Tensor) -> torch.Tensor:
        """Query attends to context. Both: (batch, seq_len, d_model)."""
        out, _ = self.attn(query, context, context)
        return self.norm(query + out)


class MultiModalFusion(nn.Module):
    """Fuse representations from multiple sensor channels.

    Architecture:
    1. Per-channel encoding (ChannelEncoder)
    2. Cross-modal attention (each channel attends to others)
    3. Aggregation (concatenation + projection)

    This enables learning dependencies like:
    - Motion → ECG artifact correlation
    - Heart rate ↔ activity level coupling
    - Posture → signal morphology changes
    """

    def __init__(
        self,
        channel_configs: dict[str, int],  # name → input_dim (1 for ECG, 3 for ACC, etc.)
        d_model: int = 64,
        n_layers: int = 2,
        n_classes: int = 0,
        dropout: float = 0.1,
    ):
        super().__init__()
        _require_torch()

        self.channel_names = list(channel_configs.keys())
        self.encoders = nn.ModuleDict({
            name: ChannelEncoder(in_dim, d_model)
            for name, in_dim in channel_configs.items()
        })

        # Cross-modal attention layers
        self.cross_attn_layers = nn.ModuleList([
            nn.ModuleDict({
                name: CrossModalAttention(d_model, dropout=dropout)
                for name in self.channel_names
            })
            for _ in range(n_layers)
        ])

        # Fusion
        n_channels = len(channel_configs)
        self.fusion = nn.Linear(d_model * n_channels, d_model)
        self.norm_out = nn.LayerNorm(d_model)

        self.n_classes = n_classes
        if n_classes > 0:
            self.classifier = nn.Sequential(
                nn.Linear(d_model, d_model),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(d_model, n_classes),
            )

    def forward(self, inputs: dict[str, torch.Tensor]) -> torch.Tensor:
        """Forward pass.

        Parameters
        ----------
        inputs : dict mapping channel name → (batch, seq_len, in_dim) tensor

        Returns
        -------
        If n_classes > 0: (batch, n_classes) logits
        Else: (batch, seq_len, d_model) fused features
        """
        # Encode each channel
        encoded = {name: self.encoders[name](inputs[name]) for name in self.channel_names}

        # Cross-modal attention
        for layer in self.cross_attn_layers:
            new_encoded = {}
            for name in self.channel_names:
                # Concatenate all other channels as context
                others = [encoded[n] for n in self.channel_names if n != name]
                if others:
                    context = torch.cat(others, dim=1)
                    new_encoded[name] = layer[name](encoded[name], context)
                else:
                    new_encoded[name] = encoded[name]
            encoded = new_encoded

        # Truncate to shortest sequence and fuse
        min_len = min(e.shape[1] for e in encoded.values())
        truncated = [encoded[name][:, :min_len, :] for name in self.channel_names]
        fused = self.fusion(torch.cat(truncated, dim=-1))
        fused = self.norm_out(fused)

        if self.n_classes > 0:
            return self.classifier(fused.mean(dim=1))

        return fused

    def get_cross_channel_attention(self, inputs: dict[str, torch.Tensor]) -> dict:
        """Extract cross-channel attention weights for interpretability."""
        encoded = {name: self.encoders[name](inputs[name]) for name in self.channel_names}

        attention_maps = {}
        for layer_idx, layer in enumerate(self.cross_attn_layers):
            for name in self.channel_names:
                others = [encoded[n] for n in self.channel_names if n != name]
                if others:
                    context = torch.cat(others, dim=1)
                    _, attn_weights = layer[name].attn(
                        encoded[name], context, context, need_weights=True
                    )
                    attention_maps[f"layer{layer_idx}_{name}"] = attn_weights.detach()

        return attention_maps
