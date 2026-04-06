"""Multi-channel time series segmentation and change-point detection.

Works with single or multiple synchronized sensor streams to detect
regime changes, segment recordings, and discover patterns.
"""

import logging
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


def detect_changepoints(
    data: np.ndarray, method: str = "pelt", penalty: float = 1.0,
    min_size: int = 10, n_bkps: Optional[int] = None,
) -> list[int]:
    """Detect change points in a single or multi-channel time series.

    Parameters
    ----------
    data : 1D or 2D array (samples × channels)
    method : "pelt" (default), "binseg", "bottomup", "window"
    penalty : penalty value for PELT (higher = fewer change points)
    min_size : minimum segment size
    n_bkps : exact number of breakpoints (for binseg/bottomup)

    Returns
    -------
    List of change point indices (sample positions)
    """
    try:
        import ruptures as rpt
    except ImportError:
        raise ImportError("ruptures is required: pip install ruptures")

    if data.ndim == 1:
        data = data.reshape(-1, 1)

    model = "rbf"  # radial basis function kernel — works for multivariate
    if method == "pelt":
        algo = rpt.Pelt(model=model, min_size=min_size).fit(data)
        bkps = algo.predict(pen=penalty)
    elif method == "binseg":
        algo = rpt.Binseg(model=model, min_size=min_size).fit(data)
        bkps = algo.predict(n_bkps=n_bkps or 5)
    elif method == "bottomup":
        algo = rpt.BottomUp(model=model, min_size=min_size).fit(data)
        bkps = algo.predict(n_bkps=n_bkps or 5)
    elif method == "window":
        algo = rpt.Window(model=model, min_size=min_size, width=min_size * 2).fit(data)
        bkps = algo.predict(n_bkps=n_bkps or 5)
    else:
        raise ValueError(f"Unknown method: {method}")

    # Remove the last breakpoint (always = len(data))
    return [b for b in bkps if b < len(data)]


def segment_multistream(
    streams: dict[str, np.ndarray],
    fs: dict[str, float],
    window_s: float = 5.0,
    method: str = "pelt",
    penalty: float = 1.0,
) -> list[dict]:
    """Segment a recording using features from multiple synchronized streams.

    Parameters
    ----------
    streams : dict mapping channel name → data array (1D or 2D)
    fs : dict mapping channel name → sampling rate
    window_s : feature extraction window in seconds
    method : change-point detection method
    penalty : PELT penalty

    Returns
    -------
    List of segments: {"start_s": float, "end_s": float, "features": dict}
    """
    # Compute features per window for each stream
    features_list = []
    max_duration = 0

    for name, data in streams.items():
        rate = fs[name]
        duration = len(data) / rate if data.ndim == 1 else data.shape[0] / rate
        max_duration = max(max_duration, duration)
        window = int(window_s * rate)
        n_windows = max(1, len(data) // window if data.ndim == 1 else data.shape[0] // window)

        stream_features = []
        for i in range(n_windows):
            s = i * window
            e = s + window
            chunk = data[s:e] if data.ndim == 1 else data[s:e]

            if data.ndim == 1 or (data.ndim == 2 and data.shape[1] == 1):
                flat = chunk.flatten()
                feats = [np.mean(flat), np.std(flat), np.max(flat) - np.min(flat)]
            else:
                # Multi-axis: mean + std per axis + magnitude stats
                feats = []
                for ax in range(chunk.shape[1]):
                    feats.extend([np.mean(chunk[:, ax]), np.std(chunk[:, ax])])
                mag = np.sqrt(np.sum(chunk ** 2, axis=1))
                feats.extend([np.mean(mag), np.std(mag)])

            stream_features.append(feats)

        features_list.append(np.array(stream_features))

    # Align to common length (min windows across streams)
    min_windows = min(f.shape[0] for f in features_list)
    combined = np.hstack([f[:min_windows] for f in features_list])

    # Detect change points on combined feature matrix
    bkps = detect_changepoints(combined, method=method, penalty=penalty, min_size=2)

    # Convert breakpoints to segments
    boundaries = [0] + bkps + [min_windows]
    segments = []
    for i in range(len(boundaries) - 1):
        start_win = boundaries[i]
        end_win = boundaries[i + 1]
        seg_features = combined[start_win:end_win]
        segments.append({
            "start_s": round(start_win * window_s, 3),
            "end_s": round(end_win * window_s, 3),
            "features": {
                "mean": seg_features.mean(axis=0).tolist(),
                "std": seg_features.std(axis=0).tolist(),
            },
        })

    return segments


def discover_patterns(
    data: np.ndarray, fs: float, pattern_length_s: float = 2.0,
    top_k: int = 5,
) -> list[dict]:
    """Discover recurring patterns (motifs) in a time series using Matrix Profile.

    Parameters
    ----------
    data : 1D or 2D array
    fs : sampling rate
    pattern_length_s : length of patterns to search for (seconds)
    top_k : number of top motifs to return

    Returns
    -------
    List of motifs: {"start_idx": int, "match_idx": int, "distance": float}
    """
    try:
        import stumpy
    except ImportError:
        raise ImportError("stumpy is required: pip install stumpy")

    m = max(4, int(pattern_length_s * fs))

    if data.ndim == 1:
        mp = stumpy.stump(data.astype(np.float64), m=m)
        # mp columns: [distance, index, left_index, right_index]
        distances = mp[:, 0].astype(float)
        indices = mp[:, 1].astype(int)
    else:
        # Multi-dimensional matrix profile
        mp, mpi = stumpy.mstump(data.T.astype(np.float64), m=m)
        distances = mp[0]  # first dimension's profile
        indices = mpi[0]

    # Find top-k motifs (lowest distances)
    sorted_idx = np.argsort(distances)
    motifs = []
    used = set()
    for idx in sorted_idx:
        if len(motifs) >= top_k:
            break
        match = int(indices[idx])
        # Avoid overlapping motifs
        if any(abs(idx - u) < m for u in used) or any(abs(match - u) < m for u in used):
            continue
        motifs.append({
            "start_idx": int(idx),
            "match_idx": match,
            "distance": round(float(distances[idx]), 4),
            "start_s": round(idx / fs, 3),
            "match_s": round(match / fs, 3),
        })
        used.add(idx)
        used.add(match)

    return motifs
