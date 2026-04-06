"""Motion signal processing: activity detection, posture, artifact detection."""

import numpy as np
from .dsp import lowpass_filter, magnitude, moving_average, rms


def classify_activity(acc: np.ndarray, fs: float, threshold: float = 0.15, window_s: float = 2.0) -> np.ndarray:
    """Classify activity vs rest from accelerometer data.

    Parameters
    ----------
    acc : Nx3 array (x, y, z) or 1D magnitude
    fs : sampling rate
    threshold : magnitude variance threshold for activity (in g)
    window_s : classification window in seconds

    Returns
    -------
    Array of labels per window: "activity" or "rest"
    """
    mag = magnitude(acc) if acc.ndim > 1 else acc

    # Remove gravity (highpass-like: subtract moving average)
    gravity = moving_average(mag, max(1, int(fs)))
    dynamic = mag - gravity

    # Windowed energy
    window = max(1, int(window_s * fs))
    energy = rms(dynamic, window)

    # Classify per-sample, then reduce to per-window
    n_windows = len(mag) // window
    labels = []
    for i in range(n_windows):
        s = i * window
        e = s + window
        win_energy = np.mean(energy[s:e])
        labels.append("activity" if win_energy > threshold else "rest")

    return np.array(labels)


def detect_posture_changes(acc: np.ndarray, fs: float, angle_threshold: float = 20.0,
                           min_duration_s: float = 1.0) -> list[dict]:
    """Detect posture changes from accelerometer orientation shifts.

    Parameters
    ----------
    acc : Nx3 array (x, y, z) in g
    fs : sampling rate
    angle_threshold : minimum angle change (degrees) to register a posture change
    min_duration_s : minimum time between consecutive detections

    Returns
    -------
    List of {"sample_idx": int, "angle_change": float}
    """
    if acc.ndim != 2 or acc.shape[1] < 3:
        return []

    # Low-pass filter to get gravity vector (posture = orientation of gravity)
    gravity = np.column_stack([
        lowpass_filter(acc[:, i], cutoff=0.5, fs=fs, order=2)
        for i in range(3)
    ])

    # Normalize gravity vectors
    norms = np.sqrt(np.sum(gravity ** 2, axis=1, keepdims=True))
    norms = np.clip(norms, 1e-6, None)
    gravity_norm = gravity / norms

    # Compute cumulative angle change over a sliding window
    # Compare orientation at time t vs time t - window
    compare_window = max(1, int(2.0 * fs))  # 2-second comparison window
    angle_changes = np.zeros(len(gravity_norm))

    for i in range(compare_window, len(gravity_norm)):
        dot = np.dot(gravity_norm[i], gravity_norm[i - compare_window])
        dot = np.clip(dot, -1.0, 1.0)
        angle_changes[i] = np.degrees(np.arccos(dot))

    # Find peaks in angle change signal
    min_distance = max(1, int(min_duration_s * fs))
    from .dsp import find_peaks
    peaks, _ = find_peaks(angle_changes, height=angle_threshold, distance=min_distance)

    return [{"sample_idx": int(p), "angle_change": round(float(angle_changes[p]), 1)} for p in peaks]


def detect_motion_artifacts(ecg: np.ndarray, acc: np.ndarray, fs_ecg: float, fs_acc: float,
                            correlation_threshold: float = 0.5, window_s: float = 1.0) -> list[dict]:
    """Detect motion artifacts by correlating ECG distortion with ACC spikes.

    Parameters
    ----------
    ecg : 1D ECG signal
    acc : Nx3 or 1D accelerometer signal
    fs_ecg : ECG sampling rate
    fs_acc : ACC sampling rate
    correlation_threshold : minimum correlation to flag as artifact
    window_s : analysis window in seconds

    Returns
    -------
    List of {"sample_idx": int, "correlation": float, "acc_energy": float}
    """
    # Compute ACC magnitude energy
    acc_mag = magnitude(acc) if acc.ndim > 1 else acc

    # Resample ACC to ECG rate if different
    if fs_acc != fs_ecg:
        from scipy import signal as sig
        target_len = int(len(acc_mag) * fs_ecg / fs_acc)
        acc_mag = sig.resample(acc_mag, target_len)

    # Ensure same length
    min_len = min(len(ecg), len(acc_mag))
    ecg = ecg[:min_len]
    acc_mag = acc_mag[:min_len]

    # ECG residual (deviation from expected morphology)
    from .dsp import bandpass_filter
    ecg_filtered = bandpass_filter(ecg, 0.5, 40.0, fs_ecg, order=3)
    ecg_residual = np.abs(ecg - ecg_filtered)

    # Dynamic ACC component (remove gravity)
    acc_dynamic = np.abs(acc_mag - moving_average(acc_mag, max(1, int(fs_ecg))))

    # Windowed correlation
    window = max(1, int(window_s * fs_ecg))
    n_windows = min_len // window
    artifacts = []

    for i in range(n_windows):
        s = i * window
        e = s + window
        ecg_win = ecg_residual[s:e]
        acc_win = acc_dynamic[s:e]

        acc_energy = float(np.mean(acc_win))
        if acc_energy < 0.05:  # no significant motion
            continue

        # Correlation
        if np.std(ecg_win) > 0 and np.std(acc_win) > 0:
            corr = float(np.corrcoef(ecg_win, acc_win)[0, 1])
            if abs(corr) > correlation_threshold:
                artifacts.append({
                    "sample_idx": int(s + window // 2),
                    "correlation": round(corr, 3),
                    "acc_energy": round(acc_energy, 4),
                })

    return artifacts
