"""ECG signal processing: R-peak detection, QRS analysis, HRV metrics."""

import numpy as np
from .dsp import bandpass_filter, derivative, find_peaks, moving_average, normalize


METHODS = ["pan_tompkins", "simple_threshold", "neurokit", "elgendi", "hamilton", "ensemble"]


def detect_r_peaks(ecg: np.ndarray, fs: float, method: str = "pan_tompkins") -> np.ndarray:
    """Detect R-peaks in an ECG signal.

    Parameters
    ----------
    ecg : 1D array of ECG samples
    fs : sampling rate in Hz
    method : detection algorithm. Options:
        - "pan_tompkins": Classic Pan-Tompkins (built-in)
        - "simple_threshold": Basic threshold approach (built-in)
        - "neurokit": NeuroKit2's default detector (requires neurokit2)
        - "elgendi": Elgendi2010 two-moving-average (requires neurokit2)
        - "hamilton": Hamilton-Tompkins (requires neurokit2)
        - "ensemble": Consensus of multiple detectors (most robust)

    Returns
    -------
    Array of sample indices where R-peaks occur
    """
    if method == "pan_tompkins":
        return _pan_tompkins(ecg, fs)
    elif method == "simple_threshold":
        return _simple_threshold(ecg, fs)
    elif method in ("neurokit", "elgendi", "hamilton"):
        return _neurokit_detect(ecg, fs, method)
    elif method == "ensemble":
        return _ensemble_detect(ecg, fs)
    else:
        raise ValueError(f"Unknown method: {method}. Available: {METHODS}")


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


# --- NeuroKit2-based detectors ---

def _neurokit_detect(ecg: np.ndarray, fs: float, method: str) -> np.ndarray:
    """R-peak detection using neurokit2 algorithms."""
    try:
        import neurokit2 as nk
    except ImportError:
        raise ImportError("neurokit2 is required for this method: pip install neurokit2")

    method_map = {"neurokit": "neurokit", "elgendi": "elgendi2010", "hamilton": "hamilton2002"}
    nk_method = method_map.get(method, "neurokit")

    cleaned = nk.ecg_clean(ecg, sampling_rate=int(fs))
    _, info = nk.ecg_peaks(cleaned, sampling_rate=int(fs), method=nk_method)
    peaks = info.get("ECG_R_Peaks", np.array([]))
    return np.array(peaks, dtype=int)


def _ensemble_detect(ecg: np.ndarray, fs: float, min_agreement: int = 2) -> np.ndarray:
    """Consensus R-peak detection using multiple algorithms.

    A peak is kept only if at least `min_agreement` detectors agree
    (within 50ms tolerance).
    """
    methods = ["pan_tompkins"]
    # Add neurokit methods if available
    try:
        import neurokit2
        methods.extend(["neurokit", "elgendi", "hamilton"])
    except ImportError:
        methods.append("simple_threshold")

    all_peaks = []
    for m in methods:
        try:
            peaks = detect_r_peaks(ecg, fs, method=m)
            all_peaks.append(set(peaks.tolist()))
        except Exception:
            continue

    if not all_peaks:
        return np.array([], dtype=int)

    # Merge: for each candidate peak, count how many detectors found one nearby
    tolerance = int(0.05 * fs)  # 50ms
    all_candidates = sorted(set().union(*all_peaks))
    consensus = []

    for candidate in all_candidates:
        votes = sum(
            1 for peak_set in all_peaks
            if any(abs(candidate - p) <= tolerance for p in peak_set)
        )
        if votes >= min_agreement:
            consensus.append(candidate)

    # Remove duplicates within tolerance
    if not consensus:
        return np.array([], dtype=int)

    consensus.sort()
    deduped = [consensus[0]]
    for c in consensus[1:]:
        if c - deduped[-1] > tolerance:
            deduped.append(c)

    return np.array(deduped, dtype=int)


def compute_bsqi(ecg: np.ndarray, fs: float) -> float:
    """Beat Signal Quality Index: agreement between two R-peak detectors.

    Returns a score 0-1 where 1 = perfect agreement (high quality).
    """
    try:
        peaks1 = detect_r_peaks(ecg, fs, method="pan_tompkins")
        peaks2 = detect_r_peaks(ecg, fs, method="simple_threshold")
    except Exception:
        return 0.0

    if len(peaks1) == 0 and len(peaks2) == 0:
        return 1.0
    if len(peaks1) == 0 or len(peaks2) == 0:
        return 0.0

    # Count matching peaks (within 50ms)
    tolerance = int(0.05 * fs)
    matched = 0
    for p1 in peaks1:
        if any(abs(p1 - p2) <= tolerance for p2 in peaks2):
            matched += 1

    total = max(len(peaks1), len(peaks2))
    return round(matched / total, 3)
