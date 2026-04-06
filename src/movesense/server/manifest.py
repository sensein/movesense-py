"""Data manifest: content-addressed storage index for collected sensor data.

Tracks all fetched data by content hash to avoid duplicates,
and maps recording time ranges to files for time-based UI navigation.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def content_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class DataManifest:
    """Tracks all data files by content hash, recording time, and device.

    Stored as manifest.json at the root of the data directory.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.manifest_file = self.data_dir / "manifest.json"
        self.entries: list[dict] = []
        self._hash_index: set[str] = set()
        self._load()

    def _load(self):
        if self.manifest_file.exists():
            try:
                data = json.loads(self.manifest_file.read_text())
                self.entries = data.get("entries", [])
                self._hash_index = {e["content_hash"] for e in self.entries if "content_hash" in e}
            except Exception as e:
                log.warning(f"Failed to load manifest: {e}")
                self.entries = []
                self._hash_index = set()

    def save(self):
        data = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "entries": self.entries,
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file.write_text(json.dumps(data, indent=2))

    def has_content(self, hash_val: str) -> bool:
        """Check if content with this hash already exists."""
        return hash_val in self._hash_index

    def get_by_hash(self, hash_val: str) -> Optional[dict]:
        """Get manifest entry by content hash."""
        for e in self.entries:
            if e.get("content_hash") == hash_val:
                return e
        return None

    def register(
        self,
        filepath: Path,
        serial: str,
        log_id: int,
        recording_start: Optional[str] = None,
        recording_end: Optional[str] = None,
        channels: Optional[list[str]] = None,
        file_type: str = "sbem",
    ) -> dict:
        """Register a data file in the manifest. Returns the entry (existing or new).

        If a file with the same content hash exists, returns the existing entry
        and the caller should skip writing (dedup).
        """
        hash_val = content_hash(filepath)

        existing = self.get_by_hash(hash_val)
        if existing:
            log.info(f"Duplicate detected: {filepath.name} matches {existing['path']} (hash: {hash_val[:12]})")
            return {**existing, "duplicate": True}

        entry = {
            "content_hash": hash_val,
            "path": str(filepath.relative_to(self.data_dir)),
            "serial": serial,
            "log_id": log_id,
            "file_type": file_type,
            "size_bytes": filepath.stat().st_size,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "recording_start": recording_start,
            "recording_end": recording_end,
            "channels": channels or [],
        }
        self.entries.append(entry)
        self._hash_index.add(hash_val)
        self.save()
        return {**entry, "duplicate": False}

    def get_time_ranges(self, serial: Optional[str] = None) -> list[dict]:
        """Get all recording time ranges, optionally filtered by device.

        Returns entries sorted by recording_start, suitable for time-based UI.
        """
        filtered = self.entries
        if serial:
            filtered = [e for e in filtered if e.get("serial") == serial]

        # Group by content_hash (dedup)
        seen = set()
        unique = []
        for e in filtered:
            h = e.get("content_hash")
            if h and h not in seen:
                seen.add(h)
                unique.append(e)

        return sorted(unique, key=lambda e: e.get("recording_start") or "")

    def rebuild_from_disk(self, scanner=None):
        """Rebuild manifest by scanning all Zarr stores on disk."""
        import zarr

        self.entries = []
        self._hash_index = set()

        for serial_dir in sorted(self.data_dir.iterdir()):
            if not serial_dir.is_dir() or serial_dir.name.startswith("."):
                continue
            serial = serial_dir.name
            for date_dir in sorted(serial_dir.iterdir()):
                if not date_dir.is_dir():
                    continue
                for f in sorted(date_dir.iterdir()):
                    if f.suffix == ".sbem" and f.stat().st_size > 0:
                        hash_val = content_hash(f)
                        if hash_val in self._hash_index:
                            continue  # skip duplicate

                        # Try to extract recording time from corresponding Zarr
                        zarr_path = f.with_suffix(".zarr")
                        recording_start = None
                        recording_end = None
                        channels = []
                        if zarr_path.exists():
                            try:
                                store = zarr.open_group(str(zarr_path), mode="r")
                                utc = store.attrs.get("utc_time", 0)
                                if utc:
                                    from datetime import datetime as dt
                                    recording_start = dt.fromtimestamp(utc / 1_000_000, tz=timezone.utc).isoformat()
                                channels = list(store.attrs.get("measurement_paths", []))
                                # Estimate duration from longest channel
                                max_dur = 0
                                for ch_name in store:
                                    grp = store[ch_name]
                                    if "data" in grp:
                                        rate = grp.attrs.get("sampling_rate_hz", 1)
                                        dur = grp["data"].shape[0] / rate
                                        max_dur = max(max_dur, dur)
                                if recording_start and max_dur > 0:
                                    from datetime import timedelta
                                    start_dt = dt.fromisoformat(recording_start)
                                    recording_end = (start_dt + timedelta(seconds=max_dur)).isoformat()
                            except Exception:
                                pass

                        # Extract log_id from filename
                        import re
                        m = re.search(r"log_(\d+)_", f.name)
                        log_id = int(m.group(1)) if m else 0

                        entry = {
                            "content_hash": hash_val,
                            "path": str(f.relative_to(self.data_dir)),
                            "serial": serial,
                            "log_id": log_id,
                            "file_type": "sbem",
                            "size_bytes": f.stat().st_size,
                            "fetched_at": date_dir.name,  # approximate from folder name
                            "recording_start": recording_start,
                            "recording_end": recording_end,
                            "channels": channels,
                        }
                        self.entries.append(entry)
                        self._hash_index.add(hash_val)

        self.save()
        log.info(f"Manifest rebuilt: {len(self.entries)} unique entries")
