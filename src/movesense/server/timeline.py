"""Timeline query: cross-session data access with gap markers."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import zarr

log = logging.getLogger(__name__)


def query_timeline(
    data_dir: Path,
    serial: str,
    start_utc_us: int,
    end_utc_us: int,
    channel: Optional[str] = None,
    buckets: int = 0,
    target_rate: Optional[float] = None,
) -> dict:
    """Query sensor data across multiple sessions by UTC time range.

    Parameters
    ----------
    data_dir : root data directory (e.g., ~/dbp/data/movesense)
    serial : device serial number
    start_utc_us : query start in UTC microseconds since epoch
    end_utc_us : query end in UTC microseconds since epoch
    channel : optional channel filter (e.g., "MeasEcgmV")
    buckets : downsample target (0 = raw data)
    target_rate : resample all segments to this Hz (None = native rate)

    Returns
    -------
    dict with "segments" list containing data segments and gap markers
    """
    store_path = Path(data_dir) / serial / "data.zarr"
    if not store_path.exists():
        return {"serial": serial, "segments": [], "error": "No data store found"}

    root = zarr.open_group(str(store_path), mode="r")
    sessions_idx = dict(root.attrs.get("sessions", {}))

    if not sessions_idx:
        return {"serial": serial, "segments": []}

    # Find sessions that overlap the query range
    overlapping = []
    for idx_str, summary in sorted(sessions_idx.items(), key=lambda x: int(x[0])):
        s_start = summary.get("start_utc_us", 0)
        s_end = summary.get("end_utc_us", 0)
        if s_end <= start_utc_us or s_start >= end_utc_us:
            continue  # no overlap

        # Check if requested channel exists in this session
        if channel:
            ch_meta = summary.get("channels", {})
            if channel not in ch_meta:
                continue

        overlapping.append((int(idx_str), summary))

    # Build segments with gap markers
    segments = []
    prev_end_us = None

    for idx, summary in overlapping:
        s_start = summary.get("start_utc_us", 0)
        s_end = summary.get("end_utc_us", 0)

        # Insert gap marker if there's a gap from previous session
        if prev_end_us is not None and s_start > prev_end_us:
            gap_duration = (s_start - prev_end_us) / 1_000_000
            segments.append({
                "type": "gap",
                "start_utc": _us_to_iso(prev_end_us),
                "start_utc_us": prev_end_us,
                "end_utc": _us_to_iso(s_start),
                "end_utc_us": s_start,
                "duration_seconds": round(gap_duration, 3),
            })

        # Read channel data from this session
        segment = _read_session_segment(
            root, idx, summary, channel,
            start_utc_us, end_utc_us,
            buckets, target_rate,
        )
        if segment:
            segments.append(segment)
            prev_end_us = s_end

    start_iso = _us_to_iso(start_utc_us)
    end_iso = _us_to_iso(end_utc_us)

    return {
        "serial": serial,
        "start": start_iso,
        "end": end_iso,
        "channel": channel,
        "segments": segments,
    }


def _read_session_segment(
    root: zarr.Group,
    session_idx: int,
    summary: dict,
    channel: Optional[str],
    query_start_us: int,
    query_end_us: int,
    buckets: int,
    target_rate: Optional[float],
) -> Optional[dict]:
    """Read data from a single session group for the query range."""
    try:
        group = root[str(session_idx)]
    except KeyError:
        return None

    s_start = summary.get("start_utc_us", 0)
    s_end = summary.get("end_utc_us", 0)

    # Determine which channels to read
    if channel:
        ch_names = [channel] if channel in group else []
    else:
        ch_names = [n for n in group if isinstance(group[n], zarr.Group) and "data" in group[n]]

    if not ch_names:
        return None

    segment = {
        "session_index": session_idx,
        "start_utc": _us_to_iso(s_start),
        "start_utc_us": s_start,
        "end_utc": _us_to_iso(s_end),
        "end_utc_us": s_end,
    }

    # For single-channel queries, include data directly
    # For multi-channel, include per-channel data dict
    if channel and len(ch_names) == 1:
        ch_group = group[ch_names[0]]
        data, rate = _read_channel_data(ch_group, s_start, query_start_us, query_end_us, buckets, target_rate)
        if data is not None:
            segment["data"] = data
            segment["rate_hz"] = rate
    else:
        channels_data = {}
        for ch_name in ch_names:
            ch_group = group[ch_name]
            data, rate = _read_channel_data(ch_group, s_start, query_start_us, query_end_us, buckets, target_rate)
            if data is not None:
                channels_data[ch_name] = {"data": data, "rate_hz": rate}
        if channels_data:
            segment["channels"] = channels_data

    return segment


def _read_channel_data(
    ch_group: zarr.Group,
    session_start_us: int,
    query_start_us: int,
    query_end_us: int,
    buckets: int,
    target_rate: Optional[float],
) -> tuple:
    """Read and optionally downsample/resample channel data for a time range.

    Returns (data_dict, rate_hz) or (None, None).
    """
    if "data" not in ch_group:
        return None, None

    arr = ch_group["data"]
    rate = float(ch_group.attrs.get("sampling_rate_hz", 1.0))
    total = arr.shape[0]

    if total == 0:
        return None, None

    # Compute sample indices from UTC range
    # Session starts at session_start_us; each sample is 1/rate seconds apart
    offset_start_s = max(0, (query_start_us - session_start_us) / 1_000_000)
    offset_end_s = (query_end_us - session_start_us) / 1_000_000

    start_idx = max(0, int(offset_start_s * rate))
    end_idx = min(total, int(offset_end_s * rate))

    if start_idx >= end_idx:
        return None, None

    chunk = arr[start_idx:end_idx]
    n = chunk.shape[0]
    actual_rate = rate

    # Resample if target_rate specified
    if target_rate and target_rate != rate and n > 1:
        chunk = _resample(chunk, rate, target_rate)
        n = chunk.shape[0]
        actual_rate = target_rate

    # Build time array (seconds relative to segment start)
    time_arr = (np.arange(n) / actual_rate).tolist()

    # Downsample if buckets specified
    if buckets > 0 and n > buckets:
        return _downsample(chunk, time_arr, buckets), actual_rate

    # Return raw data
    if chunk.ndim == 1:
        return {"time": time_arr, "values": chunk.tolist(), "columns": ["values"]}, actual_rate
    else:
        cols = ["x", "y", "z", "a", "b", "c", "d", "e", "f"][:chunk.shape[1]]
        data = {"time": time_arr, "columns": cols}
        for i, col in enumerate(cols):
            data[col] = chunk[:, i].tolist()
        return data, actual_rate


def _resample(data: np.ndarray, source_rate: float, target_rate: float) -> np.ndarray:
    """Resample data from source_rate to target_rate using linear interpolation."""
    n_source = data.shape[0]
    duration = n_source / source_rate
    n_target = int(duration * target_rate)

    if n_target <= 0:
        return data

    source_times = np.linspace(0, duration, n_source)
    target_times = np.linspace(0, duration, n_target)

    if data.ndim == 1:
        return np.interp(target_times, source_times, data).astype(data.dtype)
    else:
        result = np.zeros((n_target, data.shape[1]), dtype=data.dtype)
        for col in range(data.shape[1]):
            result[:, col] = np.interp(target_times, source_times, data[:, col])
        return result


def _downsample(data: np.ndarray, time_arr: list, buckets: int) -> dict:
    """Downsample to bucket count with min/max/mean per bucket."""
    n = data.shape[0]
    bucket_size = n / buckets

    if data.ndim == 1:
        times, means = [], []
        for b in range(buckets):
            s = int(b * bucket_size)
            e = int((b + 1) * bucket_size)
            chunk = data[s:e]
            if len(chunk) == 0:
                continue
            times.append(time_arr[s])
            means.append(round(float(np.mean(chunk)), 6))
        return {"time": times, "values": means, "columns": ["values"]}
    else:
        cols = ["x", "y", "z", "a", "b", "c", "d", "e", "f"][:data.shape[1]]
        result = {"time": [], "columns": cols}
        for col in cols:
            result[col] = []
        for b in range(buckets):
            s = int(b * bucket_size)
            e = int((b + 1) * bucket_size)
            chunk = data[s:e]
            if len(chunk) == 0:
                continue
            result["time"].append(time_arr[s])
            for i, col in enumerate(cols):
                result[col].append(round(float(np.mean(chunk[:, i])), 6))
        return result


def _us_to_iso(us: int) -> str:
    """Convert UTC microseconds to ISO 8601 string with µs precision."""
    dt = datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
