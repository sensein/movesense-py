"""Learned models for physiological signal processing.

Physics-informed and data-driven models using PyTorch.
Supports MPS (macOS), CUDA, and CPU backends.

Models:
- SSM: State-space model backbone (Mamba-style) for biosignal sequence modeling
- CausalDiscovery: PCMCI+ and neural Granger causality for cross-sensor dependencies
- PhysicsRNN: Physics-informed recurrent model (WARP-style) for dynamical systems
- MultiModalEncoder: Cross-sensor representation learning
"""

from ..import get_device
