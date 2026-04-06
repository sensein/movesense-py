"""Causal discovery across multi-sensor physiological streams.

Identifies directed causal relationships between sensor channels
(e.g., motion → ECG artifact, heart rate → temperature response).
"""

import logging
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


def granger_causality_test(
    x: np.ndarray, y: np.ndarray, max_lag: int = 10, alpha: float = 0.05,
) -> dict:
    """Test if time series x Granger-causes y.

    Uses VAR model comparison (restricted vs unrestricted) with F-test.

    Parameters
    ----------
    x : 1D array (potential cause)
    y : 1D array (potential effect)
    max_lag : maximum lag to test
    alpha : significance level

    Returns
    -------
    dict with keys: is_causal, best_lag, p_value, f_statistic
    """
    from scipy import stats

    n = min(len(x), len(y))
    x, y = x[:n], y[:n]

    best_result = {"is_causal": False, "best_lag": 0, "p_value": 1.0, "f_statistic": 0.0}

    for lag in range(1, max_lag + 1):
        if lag >= n - 2:
            break

        # Restricted model: y[t] = sum(a_i * y[t-i])
        Y = y[lag:]
        X_r = np.column_stack([y[lag - i - 1: n - i - 1] for i in range(lag)])

        # Unrestricted model: y[t] = sum(a_i * y[t-i]) + sum(b_i * x[t-i])
        X_u = np.column_stack([
            X_r,
            *[x[lag - i - 1: n - i - 1] for i in range(lag)],
        ])

        # Fit both models via OLS
        try:
            beta_r = np.linalg.lstsq(X_r, Y, rcond=None)[0]
            beta_u = np.linalg.lstsq(X_u, Y, rcond=None)[0]

            rss_r = np.sum((Y - X_r @ beta_r) ** 2)
            rss_u = np.sum((Y - X_u @ beta_u) ** 2)

            n_obs = len(Y)
            p_r = X_r.shape[1]
            p_u = X_u.shape[1]

            if rss_u <= 0 or n_obs <= p_u + 1:
                continue

            f_stat = ((rss_r - rss_u) / (p_u - p_r)) / (rss_u / (n_obs - p_u - 1))
            p_value = 1 - stats.f.cdf(f_stat, p_u - p_r, n_obs - p_u - 1)

            if p_value < best_result["p_value"]:
                best_result = {
                    "is_causal": p_value < alpha,
                    "best_lag": lag,
                    "p_value": round(float(p_value), 6),
                    "f_statistic": round(float(f_stat), 4),
                }
        except np.linalg.LinAlgError:
            continue

    return best_result


def cross_channel_causality(
    streams: dict[str, np.ndarray],
    fs: dict[str, float],
    max_lag_s: float = 2.0,
    alpha: float = 0.05,
) -> list[dict]:
    """Discover causal relationships between all pairs of sensor channels.

    Parameters
    ----------
    streams : dict mapping channel name → 1D array
    fs : dict mapping channel name → sampling rate
    max_lag_s : maximum causal lag in seconds
    alpha : significance threshold

    Returns
    -------
    List of directed edges: {"cause": str, "effect": str, "lag_s": float, "p_value": float, "strength": float}
    """
    from scipy.signal import resample

    channels = list(streams.keys())
    # Resample all to common rate (min rate for efficiency)
    common_fs = min(fs.values())
    resampled = {}
    for name in channels:
        data = streams[name]
        if data.ndim > 1:
            data = np.sqrt(np.sum(data ** 2, axis=1))  # magnitude for multi-axis
        target_len = int(len(data) * common_fs / fs[name])
        resampled[name] = resample(data, target_len)

    max_lag = max(1, int(max_lag_s * common_fs))
    edges = []

    for cause in channels:
        for effect in channels:
            if cause == effect:
                continue
            result = granger_causality_test(
                resampled[cause], resampled[effect],
                max_lag=max_lag, alpha=alpha,
            )
            if result["is_causal"]:
                edges.append({
                    "cause": cause,
                    "effect": effect,
                    "lag_s": round(result["best_lag"] / common_fs, 3),
                    "p_value": result["p_value"],
                    "strength": result["f_statistic"],
                })

    return sorted(edges, key=lambda e: e["p_value"])


def compute_transfer_entropy(
    x: np.ndarray, y: np.ndarray, lag: int = 1, bins: int = 10,
) -> float:
    """Compute transfer entropy from x to y (information-theoretic causality).

    TE(X→Y) = H(Y_t | Y_{t-1}) - H(Y_t | Y_{t-1}, X_{t-lag})

    Higher TE indicates stronger directed information flow from X to Y.
    """
    n = min(len(x), len(y)) - lag
    if n < 10:
        return 0.0

    # Discretize into bins
    x_binned = np.digitize(x[:n], np.linspace(x.min(), x.max(), bins))
    y_binned = np.digitize(y[lag:lag + n], np.linspace(y.min(), y.max(), bins))
    y_past = np.digitize(y[:n], np.linspace(y.min(), y.max(), bins))

    # Joint and conditional entropies via histogram counts
    def _entropy(*arrays):
        combined = np.column_stack(arrays)
        _, counts = np.unique(combined, axis=0, return_counts=True)
        probs = counts / counts.sum()
        return -np.sum(probs * np.log2(probs + 1e-10))

    h_y_ypast = _entropy(y_binned, y_past)
    h_y_ypast_x = _entropy(y_binned, y_past, x_binned)
    h_ypast = _entropy(y_past)
    h_ypast_x = _entropy(y_past, x_binned)

    te = (h_y_ypast - h_ypast) - (h_y_ypast_x - h_ypast_x)
    return round(float(max(0, te)), 6)
