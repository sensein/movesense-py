"""Scan data directory and build index of devices/dates/sessions/channels."""

import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np
import zarr

log = logging.getLogger(__name__)

# Pattern: Movesense_log_{id}_{serial}.zarr
LOG_PATTERN = re.compile(r"Movesense_log_(\d+)_(.+)\.zarr$")


class DataScanner:
    """Scans ~/dbp/data/movesense/ and indexes available sensor data."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.devices: list[dict] = []
        self._index: dict[str, dict] = {}  # serial -> {dates -> {date -> [sessions]}}

    def scan(self) -> None:
        """Scan the data directory and rebuild the index.

        Supports two layouts:
        1. New: {serial}/data.zarr with indexed session groups (preferred)
        2. Legacy: {serial}/{date}/Movesense_log_{id}_{serial}.zarr (per-session)
        """
        self.devices = []
        self._index = {}

        if not self.data_dir.exists():
            log.warning(f"Data directory does not exist: {self.data_dir}")
            return

        for serial_dir in sorted(self.data_dir.iterdir()):
            if not serial_dir.is_dir() or serial_dir.name.startswith("."):
                continue

            serial = serial_dir.name
            dates = []

            # Check for new DeviceStore layout first
            device_store_path = serial_dir / "data.zarr"
            if device_store_path.exists():
                dates = self._scan_device_store(serial, device_store_path)
            else:
                # Legacy: scan per-date directories
                for date_dir in sorted(serial_dir.iterdir()):
                    if not date_dir.is_dir() or not re.match(r"\d{4}-\d{2}-\d{2}$", date_dir.name):
                        continue

                    sessions = self._scan_sessions(date_dir)
                    if sessions:
                        dates.append(date_dir.name)
                        self._index.setdefault(serial, {})[date_dir.name] = sessions

            if dates:
                self.devices.append({"serial": serial, "date_count": len(dates)})

    def _scan_device_store(self, serial: str, store_path: Path) -> list[str]:
        """Index sessions from a DeviceStore (data.zarr with session groups)."""
        dates = []
        try:
            root = zarr.open_group(str(store_path), mode="r")
            sessions_idx = dict(root.attrs.get("sessions", {}))

            for idx_str, summary in sorted(sessions_idx.items(), key=lambda x: int(x[0])):
                idx = int(idx_str)
                start_utc = summary.get("start_utc", "")
                date_str = start_utc[:10] if len(start_utc) >= 10 else "unknown"

                # Extract channel details from the session group
                try:
                    group = root[idx_str]
                    channels = self._extract_channels(group)
                except Exception:
                    channels = []

                session = {
                    "log_id": idx,
                    "zarr_path": str(store_path),
                    "zarr_session_index": idx,
                    "channels": [c["name"] for c in channels],
                    "channel_details": channels,
                    "root_attrs": dict(summary),
                    "has_csv": False,
                    "has_json": False,
                }

                self._index.setdefault(serial, {}).setdefault(date_str, []).append(session)
                if date_str not in dates:
                    dates.append(date_str)

        except Exception as e:
            log.warning(f"Error reading DeviceStore {store_path}: {e}")

        return sorted(dates)

    def _scan_sessions(self, date_dir: Path) -> list[dict]:
        """Scan a date directory for Zarr log sessions."""
        sessions = []

        for zarr_dir in sorted(date_dir.iterdir()):
            if not zarr_dir.is_dir():
                continue
            match = LOG_PATTERN.match(zarr_dir.name)
            if not match:
                continue

            log_id = int(match.group(1))
            serial = match.group(2)

            try:
                store = zarr.open_group(str(zarr_dir), mode="r")
                channels = self._extract_channels(store)
                session = {
                    "log_id": log_id,
                    "zarr_path": str(zarr_dir),
                    "channels": [c["name"] for c in channels],
                    "channel_details": channels,
                    "root_attrs": dict(store.attrs),
                    "has_csv": (date_dir / zarr_dir.name.replace(".zarr", ".csv")).exists(),
                    "has_json": (date_dir / zarr_dir.name.replace(".zarr", ".json")).exists(),
                }
                sessions.append(session)
            except Exception as e:
                log.warning(f"Skipping corrupted Zarr store {zarr_dir}: {e}")
                continue

        return sessions

    def _extract_channels(self, store: zarr.Group) -> list[dict]:
        """Extract channel metadata from a Zarr store."""
        channels = []
        for name in store:
            group = store[name]
            if not isinstance(group, zarr.Group):
                continue

            channel = {"name": name, "sensor_type": group.attrs.get("sensor_type", name)}

            if "sampling_rate_hz" in group.attrs:
                channel["sampling_rate_hz"] = group.attrs["sampling_rate_hz"]
            if "unit" in group.attrs:
                channel["unit"] = group.attrs["unit"]

            if "data" in group:
                arr = group["data"]
                channel["shape"] = list(arr.shape)
                channel["dtype"] = str(arr.dtype)
                channel["sample_count"] = arr.shape[0]
            else:
                channel["shape"] = []
                channel["dtype"] = "unknown"
                channel["sample_count"] = 0

            channels.append(channel)
        return channels

    def get_dates(self, serial: str) -> Optional[list[str]]:
        """Get available dates for a device."""
        if serial not in self._index:
            return None
        return sorted(self._index[serial].keys())

    def get_sessions(self, serial: str, date: str) -> Optional[list[dict]]:
        """Get sessions for a device on a date."""
        if serial not in self._index or date not in self._index.get(serial, {}):
            return None
        return self._index[serial][date]

    def get_channels(self, serial: str, date: str, log_id: int) -> Optional[list[dict]]:
        """Get channel metadata for a specific session."""
        sessions = self.get_sessions(serial, date)
        if sessions is None:
            return None
        for s in sessions:
            if s["log_id"] == log_id:
                return s["channel_details"]
        return None

    def get_channel_data(
        self, serial: str, date: str, log_id: int, channel_name: str,
        offset: int = 0, limit: int = 10000,
    ) -> Optional[dict]:
        """Read channel data from Zarr store with pagination."""
        sessions = self.get_sessions(serial, date)
        if sessions is None:
            return None

        session = next((s for s in sessions if s["log_id"] == log_id), None)
        if session is None:
            return None

        try:
            store = zarr.open_group(session["zarr_path"], mode="r")
            # DeviceStore: navigate to session group first
            if "zarr_session_index" in session:
                store = store[str(session["zarr_session_index"])]

            if channel_name not in store:
                return None

            group = store[channel_name]
            if "data" not in group:
                return None

            arr = group["data"]
            total = arr.shape[0]
            end = min(offset + limit, total)
            chunk = arr[offset:end]

            # Convert to list (handle multi-dimensional)
            data = chunk.tolist()

            result = {
                "channel": channel_name,
                "sensor_type": group.attrs.get("sensor_type", channel_name),
                "offset": offset,
                "limit": limit,
                "total_samples": total,
                "data": data,
            }
            if "sampling_rate_hz" in group.attrs:
                result["sampling_rate_hz"] = group.attrs["sampling_rate_hz"]
            if "unit" in group.attrs:
                result["unit"] = group.attrs["unit"]
            if chunk.ndim > 1:
                result["shape"] = list(arr.shape)

            return result

        except Exception as e:
            log.error(f"Error reading channel data: {e}")
            return None

    def compute_coverage(self, serial: str, year: int, month: int, threshold_hours: float = 8.0) -> Optional[dict]:
        """Compute per-day data coverage for a device in a given month."""
        dates = self.get_dates(serial)
        if dates is None:
            return None

        prefix = f"{year:04d}-{month:02d}"
        days = []
        for date_str in dates:
            if not date_str.startswith(prefix):
                continue
            sessions = self.get_sessions(serial, date_str)
            if not sessions:
                continue

            total_duration = 0.0
            all_channels = set()
            for s in sessions:
                for ch in s.get("channel_details", []):
                    all_channels.add(ch["name"])
                    rate = ch.get("sampling_rate_hz", 0)
                    count = ch.get("sample_count", 0)
                    if rate > 0:
                        dur = count / rate
                        total_duration = max(total_duration, dur)  # longest channel = session duration

            hours = total_duration / 3600
            level = "substantial" if hours >= threshold_hours else ("partial" if hours > 0 else "none")

            days.append({
                "date": date_str,
                "session_count": len(sessions),
                "total_duration_s": round(total_duration, 1),
                "channels": sorted(all_channels),
                "level": level,
            })

        # Summary
        days_with_data = len(days)
        total_hours = sum(d["total_duration_s"] for d in days) / 3600
        avg_daily = total_hours / days_with_data if days_with_data else 0

        # Longest gap
        sorted_dates = sorted(d["date"] for d in days)
        longest_gap = 0
        for i in range(1, len(sorted_dates)):
            from datetime import date as dt_date
            d1 = dt_date.fromisoformat(sorted_dates[i - 1])
            d2 = dt_date.fromisoformat(sorted_dates[i])
            gap = (d2 - d1).days - 1
            longest_gap = max(longest_gap, gap)

        return {
            "serial": serial,
            "year": year,
            "month": month,
            "days": days,
            "summary": {
                "days_with_data": days_with_data,
                "total_hours": round(total_hours, 1),
                "avg_daily_hours": round(avg_daily, 1),
                "longest_gap_days": longest_gap,
            },
        }

    def downsample_channel(
        self, serial: str, date: str, log_id: int, channel_name: str,
        start: float = 0, end: Optional[float] = None, buckets: int = 1000,
    ) -> Optional[dict]:
        """Return downsampled min/max/mean per time bucket for a channel."""
        sessions = self.get_sessions(serial, date)
        if sessions is None:
            return None
        session = next((s for s in sessions if s["log_id"] == log_id), None)
        if session is None:
            return None

        try:
            store = zarr.open_group(session["zarr_path"], mode="r")
            # DeviceStore: navigate to session group first
            if "zarr_session_index" in session:
                store = store[str(session["zarr_session_index"])]
            if channel_name not in store:
                return None
            group = store[channel_name]
            if "data" not in group:
                return None

            arr = group["data"][:]
            rate = group.attrs.get("sampling_rate_hz", 1.0)
            total = arr.shape[0]

            # Time range selection
            start_idx = max(0, int(start * rate))
            end_idx = int(end * rate) if end is not None else total
            end_idx = min(end_idx, total)
            arr = arr[start_idx:end_idx]
            n = arr.shape[0]

            if n == 0:
                return {"channel": channel_name, "data": {}, "total_samples": total}

            duration = n / rate
            actual_start = start_idx / rate
            actual_end = end_idx / rate

            result = {
                "channel": channel_name,
                "start": round(actual_start, 6),
                "end": round(actual_end, 6),
                "buckets": buckets,
                "total_samples": total,
                "sampling_rate_hz": rate,
            }

            # If fewer samples than buckets, return raw data
            if n <= buckets:
                time_arr = (np.arange(n) / rate + actual_start).tolist()
                if arr.ndim == 1:
                    result["data"] = {"time": time_arr, "values": arr.tolist()}
                else:
                    result["data"] = {"time": time_arr}
                    cols = ["x", "y", "z", "a", "b", "c", "d", "e", "f"][:arr.shape[1]]
                    result["columns"] = cols
                    for i, col in enumerate(cols):
                        result["data"][col] = arr[:, i].tolist()
                return result

            # Downsample: compute min/max/mean per bucket
            bucket_size = n / buckets
            time_arr = []

            if arr.ndim == 1:
                mins, maxs, means = [], [], []
                for b in range(buckets):
                    s = int(b * bucket_size)
                    e = int((b + 1) * bucket_size)
                    chunk = arr[s:e]
                    if len(chunk) == 0:
                        continue
                    time_arr.append(round((s / rate) + actual_start, 6))
                    mins.append(round(float(np.min(chunk)), 6))
                    maxs.append(round(float(np.max(chunk)), 6))
                    means.append(round(float(np.mean(chunk)), 6))
                result["data"] = {"time": time_arr, "min": mins, "max": maxs, "mean": means}
            else:
                cols = ["x", "y", "z", "a", "b", "c", "d", "e", "f"][:arr.shape[1]]
                result["columns"] = cols
                col_data: dict = {"time": []}
                for col in cols:
                    col_data[f"{col}_min"] = []
                    col_data[f"{col}_max"] = []
                    col_data[f"{col}_mean"] = []

                for b in range(buckets):
                    s = int(b * bucket_size)
                    e = int((b + 1) * bucket_size)
                    chunk = arr[s:e]
                    if len(chunk) == 0:
                        continue
                    col_data["time"].append(round((s / rate) + actual_start, 6))
                    for i, col in enumerate(cols):
                        col_data[f"{col}_min"].append(round(float(np.min(chunk[:, i])), 6))
                        col_data[f"{col}_max"].append(round(float(np.max(chunk[:, i])), 6))
                        col_data[f"{col}_mean"].append(round(float(np.mean(chunk[:, i])), 6))

                result["data"] = col_data

            return result

        except Exception as e:
            log.error(f"Downsample error: {e}")
            return None

    def get_session_metadata(self, serial: str, date: str, log_id: int) -> Optional[dict]:
        """Get root metadata for a session."""
        sessions = self.get_sessions(serial, date)
        if sessions is None:
            return None
        session = next((s for s in sessions if s["log_id"] == log_id), None)
        if session is None:
            return None
        return session.get("root_attrs", {})
