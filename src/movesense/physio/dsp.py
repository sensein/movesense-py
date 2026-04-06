"""Standard digital signal processing functions.

These are general-purpose building blocks used by the physics-based detectors.
All functions operate on numpy arrays.
"""

import numpy as np
from scipy import signal as sig


def bandpass_filter(data: np.ndarray, lowcut: float, highcut: float, fs: float, order: int = 4) -> np.ndarray:
    """Apply a Butterworth bandpass filter."""
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    sos = sig.butter(order, [low, high], btype="band", output="sos")
    return sig.sosfiltfilt(sos, data)


def lowpass_filter(data: np.ndarray, cutoff: float, fs: float, order: int = 4) -> np.ndarray:
    """Apply a Butterworth lowpass filter."""
    nyq = 0.5 * fs
    sos = sig.butter(order, cutoff / nyq, btype="low", output="sos")
    return sig.sosfiltfilt(sos, data)


def highpass_filter(data: np.ndarray, cutoff: float, fs: float, order: int = 4) -> np.ndarray:
    """Apply a Butterworth highpass filter."""
    nyq = 0.5 * fs
    sos = sig.butter(order, cutoff / nyq, btype="high", output="sos")
    return sig.sosfiltfilt(sos, data)


def envelope(data: np.ndarray, fs: float, cutoff: float = 5.0) -> np.ndarray:
    """Compute the signal envelope using Hilbert transform + lowpass."""
    analytic = sig.hilbert(data)
    env = np.abs(analytic)
    return lowpass_filter(env, cutoff, fs, order=2)


def find_peaks(data: np.ndarray, height=None, distance=None, prominence=None, threshold=None) -> tuple:
    """Find peaks in a signal. Wrapper around scipy.signal.find_peaks."""
    return sig.find_peaks(data, height=height, distance=distance, prominence=prominence, threshold=threshold)


def zero_crossings(data: np.ndarray) -> np.ndarray:
    """Find zero-crossing indices."""
    return np.where(np.diff(np.sign(data)))[0]


def rms(data: np.ndarray, window: int) -> np.ndarray:
    """Compute rolling root-mean-square."""
    squared = data ** 2
    kernel = np.ones(window) / window
    return np.sqrt(np.convolve(squared, kernel, mode="same"))


def magnitude(data: np.ndarray) -> np.ndarray:
    """Compute vector magnitude for multi-axis data (Nx3 → N)."""
    if data.ndim == 1:
        return np.abs(data)
    return np.sqrt(np.sum(data ** 2, axis=1))


def normalize(data: np.ndarray) -> np.ndarray:
    """Z-score normalize a signal."""
    std = np.std(data)
    if std == 0:
        return data - np.mean(data)
    return (data - np.mean(data)) / std


def moving_average(data: np.ndarray, window: int) -> np.ndarray:
    """Compute a simple moving average."""
    kernel = np.ones(window) / window
    return np.convolve(data, kernel, mode="same")


def derivative(data: np.ndarray, fs: float) -> np.ndarray:
    """Compute the first derivative (gradient)."""
    return np.gradient(data, 1.0 / fs)


def power_spectral_density(data: np.ndarray, fs: float, nperseg: int = 256) -> tuple:
    """Compute power spectral density using Welch's method."""
    return sig.welch(data, fs=fs, nperseg=nperseg)
