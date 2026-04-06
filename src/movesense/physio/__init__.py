"""Physiological signal processing library.

Portable collection of physics-based and standard detection algorithms
for ECG, accelerometer, gyroscope, and multi-modal sensor data.
Designed to be moved to senselab when it adds physiological signal support.
"""

import logging

log = logging.getLogger(__name__)


def get_device():
    """Auto-detect best available compute device (MPS > CUDA > CPU)."""
    try:
        import torch
        if torch.backends.mps.is_available():
            return torch.device("mps")
        if torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")
    except ImportError:
        return "cpu"  # numpy-only fallback
