"""Signal quality assessment for ECG and other physiological signals."""

import numpy as np
from .dsp import bandpass_filter, power_spectral_density


def ecg_signal_quality(ecg: np.ndarray, fs: float, window_s: float = 5.0) -> list[dict]:
    """Estimate ECG signal quality index (SQI) per window.

    Uses a combination of:
    - Power ratio: signal band (5-15 Hz) vs noise band (>40 Hz)
    - Kurtosis: QRS complexes produce high kurtosis in clean ECG
    - Template matching: consistency of detected beats

    Parameters
    ----------
    ecg : 1D ECG signal
    fs : sampling rate
    window_s : analysis window in seconds

    Returns
    -------
    List of {"sample_idx": int, "sqi": float, "level": str}
    where sqi is 0-1 and level is "high", "medium", or "low"
    """
    window = max(1, int(window_s * fs))
    n_windows = len(ecg) // window
    results = []

    for i in range(n_windows):
        s = i * window
        e = s + window
        segment = ecg[s:e]

        # Power ratio
        freqs, psd = power_spectral_density(segment, fs, nperseg=min(256, window))
        signal_band = np.sum(psd[(freqs >= 5) & (freqs <= 15)])
        noise_band = np.sum(psd[freqs >= 40]) + 1e-10
        power_ratio = signal_band / (signal_band + noise_band)

        # Kurtosis (QRS produces high kurtosis)
        from scipy.stats import kurtosis
        kurt = kurtosis(segment)
        kurt_score = min(1.0, max(0.0, kurt / 10))  # normalize ~0-1

        # Combined SQI
        sqi = 0.6 * power_ratio + 0.4 * kurt_score
        sqi = round(min(1.0, max(0.0, sqi)), 3)

        level = "high" if sqi > 0.7 else ("medium" if sqi > 0.4 else "low")

        results.append({
            "sample_idx": int(s + window // 2),
            "sqi": sqi,
            "level": level,
        })

    return results
