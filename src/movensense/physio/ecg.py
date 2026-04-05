"""ECG signal processing: R-peak detection, QRS analysis, HRV metrics."""

import numpy as np
from .dsp import bandpass_filter, derivative, find_peaks, moving_average, normalize


def detect_r_peaks(ecg: np.ndarray, fs: float, method: str = "pan_tompkins") -> np.ndarray:
    """Detect R-peaks in an ECG signal.

    Parameters
    ----------
    ecg : 1D array of ECG samples
    fs : sampling rate in Hz
    method : detection algorithm ("pan_tompkins" or "simple_threshold")

    Returns
    -------
    Array of sample indices where R-peaks occur
    """
    if method == "pan_tompkins":
        return _pan_tompkins(ecg, fs)
    elif method == "simple_threshold":
        return _simple_threshold(ecg, fs)
    else:
        raise ValueError(f"Unknown method: {method}")


def _pan_tompkins(ecg: np.ndarray, fs: float) -> np.ndarray:
    """Pan-Tompkins R-peak detection algorithm.

    1. Bandpass filter (5-15 Hz)
    2. Differentiate
    3. Square
    4. Moving window integration
    5. Adaptive thresholding
    """
    # Bandpass 5-15 Hz
    filtered = bandpass_filter(ecg, 5.0, 15.0, fs, order=2)

    # Differentiate
    diff = derivative(filtered, fs)

    # Square
    squared = diff ** 2

    # Moving window integration (150ms window)
    window = max(1, int(0.15 * fs))
    integrated = moving_average(squared, window)

    # Find peaks with minimum distance (200ms = 300bpm max)
    min_distance = max(1, int(0.2 * fs))
    peaks, properties = find_peaks(integrated, distance=min_distance)

    if len(peaks) == 0:
        return np.array([], dtype=int)

    # Adaptive threshold: 0.3 × mean of top peaks
    peak_heights = integrated[peaks]
    threshold = 0.3 * np.mean(np.sort(peak_heights)[-max(1, len(peak_heights) // 4):])
    peaks = peaks[peak_heights > threshold]

    # Refine: find actual R-peak in original signal near each detected peak
    refined = []
    search_window = int(0.075 * fs)  # 75ms search window
    for p in peaks:
        start = max(0, p - search_window)
        end = min(len(ecg), p + search_window)
        local_max = start + np.argmax(ecg[start:end])
        refined.append(local_max)

    return np.array(sorted(set(refined)), dtype=int)


def _simple_threshold(ecg: np.ndarray, fs: float) -> np.ndarray:
    """Simple threshold-based R-peak detection."""
    filtered = bandpass_filter(ecg, 5.0, 30.0, fs, order=3)
    threshold = np.std(filtered) * 1.5
    min_distance = int(0.3 * fs)
    peaks, _ = find_peaks(filtered, height=threshold, distance=min_distance)
    return peaks


def compute_rr_intervals(r_peaks: np.ndarray, fs: float) -> np.ndarray:
    """Compute R-R intervals in milliseconds from R-peak indices."""
    if len(r_peaks) < 2:
        return np.array([])
    return np.diff(r_peaks) / fs * 1000  # ms


def compute_heart_rate(rr_intervals: np.ndarray) -> np.ndarray:
    """Compute instantaneous heart rate (bpm) from R-R intervals (ms)."""
    valid = rr_intervals[rr_intervals > 0]
    return 60000.0 / valid  # bpm


def compute_hrv(rr_intervals: np.ndarray) -> dict:
    """Compute heart rate variability metrics from R-R intervals (ms).

    Returns
    -------
    dict with keys: sdnn, rmssd, pnn50, mean_hr, std_hr, mean_rr
    """
    rr = rr_intervals[rr_intervals > 200]  # filter out unrealistic intervals
    rr = rr[rr < 2000]

    if len(rr) < 2:
        return {"sdnn": 0, "rmssd": 0, "pnn50": 0, "mean_hr": 0, "std_hr": 0, "mean_rr": 0}

    diffs = np.diff(rr)
    hr = 60000.0 / rr

    return {
        "sdnn": round(float(np.std(rr)), 2),
        "rmssd": round(float(np.sqrt(np.mean(diffs ** 2))), 2),
        "pnn50": round(float(100 * np.sum(np.abs(diffs) > 50) / len(diffs)), 2),
        "mean_hr": round(float(np.mean(hr)), 2),
        "std_hr": round(float(np.std(hr)), 2),
        "mean_rr": round(float(np.mean(rr)), 2),
    }
