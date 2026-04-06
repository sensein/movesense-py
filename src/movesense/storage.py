"""Content-addressed blob store, provenance log, and device Zarr store management.

Storage layout per device:
    {data_dir}/{serial}/
    ├── data.zarr/           # Single Zarr v3 store (all sessions)
    │   ├── 0/               # Session group (from SBEM log)
    │   ├── 1/
    │   ├── stream/          # Live-streamed data (lower trust)
    │   │   ├── 0/
    │   │   └── 1/
    ├── blobs/               # Content-addressed SBEM files
    │   └── {hash[:2]}/{hash}.sbem
    └── prov.jsonl           # Provenance log (append-only)
"""

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

BLOB_CHUNK_SIZE = 8192


# --- Content Hash ---

def content_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(BLOB_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


# --- Timestamp Normalization ---

def normalize_timestamp(value: int, source_unit: str = "ms") -> int:
    """Convert a device timestamp to microseconds (µs).

    Parameters
    ----------
    value : raw timestamp integer from device
    source_unit : "ms" or "us"

    Returns
    -------
    uint64 timestamp in microseconds
    """
    if source_unit == "ms":
        return value * 1000
    return value  # already µs


def device_ts_to_utc(ts_us: int, mapping: dict) -> int:
    """Convert a stored µs timestamp to absolute UTC µs.

    Parameters
    ----------
    ts_us : stored timestamp in µs
    mapping : dict with 'relative_time_us' and 'utc_time_us' keys

    Returns
    -------
    UTC timestamp in microseconds since epoch
    """
    return mapping["utc_time_us"] + (ts_us - mapping["relative_time_us"])


# --- Blob Store ---

class BlobStore:
    """Content-addressed SBEM file storage using SHA-256 hashes."""

    def __init__(self, device_dir: Path):
        self.blobs_dir = device_dir / "blobs"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)

    def _blob_path(self, hash_val: str) -> Path:
        return self.blobs_dir / hash_val[:2] / f"{hash_val}.sbem"

    def exists(self, hash_val: str) -> bool:
        """Check if a blob with this hash already exists."""
        return self._blob_path(hash_val).exists()

    def path(self, hash_val: str) -> Path:
        """Return the path where a blob is/would be stored."""
        return self._blob_path(hash_val)

    def store(self, sbem_path: Path) -> str:
        """Compute hash and store the SBEM file. Returns the hash.

        If the hash already exists, the file is not copied (dedup).
        """
        hash_val = content_hash(sbem_path)
        dest = self._blob_path(hash_val)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sbem_path, dest)
            log.info(f"Stored blob {hash_val[:12]}... ({sbem_path.name})")
        else:
            log.info(f"Blob {hash_val[:12]}... already exists (dedup)")
        return hash_val

    def rebuild_index(self) -> set[str]:
        """Scan blob directory and return set of all stored hashes."""
        hashes = set()
        if self.blobs_dir.exists():
            for prefix_dir in self.blobs_dir.iterdir():
                if prefix_dir.is_dir() and len(prefix_dir.name) == 2:
                    for blob_file in prefix_dir.glob("*.sbem"):
                        hashes.add(blob_file.stem)
        return hashes


# --- Provenance Log ---

class ProvLog:
    """Append-only JSONL provenance log for SBEM blobs."""

    def __init__(self, device_dir: Path):
        self.log_file = device_dir / "prov.jsonl"
        self._hash_index: Optional[set[str]] = None

    def _load_index(self) -> set[str]:
        if self._hash_index is not None:
            return self._hash_index
        self._hash_index = set()
        if self.log_file.exists():
            for line in self.log_file.read_text().strip().split("\n"):
                if line:
                    try:
                        entry = json.loads(line)
                        self._hash_index.add(entry.get("hash", ""))
                    except json.JSONDecodeError:
                        continue
        return self._hash_index

    def has_hash(self, hash_val: str) -> bool:
        """Check if a hash is already recorded."""
        return hash_val in self._load_index()

    def find_by_hash(self, hash_val: str) -> Optional[dict]:
        """Find a provenance record by hash."""
        if not self.log_file.exists():
            return None
        for line in self.log_file.read_text().strip().split("\n"):
            if line:
                try:
                    entry = json.loads(line)
                    if entry.get("hash") == hash_val:
                        return entry
                except json.JSONDecodeError:
                    continue
        return None

    def record(
        self,
        hash_val: str,
        original_filename: str,
        serial: str,
        log_id: int,
        session_index: int,
        channels: list[str],
        status: str = "ok",
        file_size_bytes: int = 0,
    ) -> dict:
        """Append a provenance record. Returns the record dict."""
        entry = {
            "hash": hash_val,
            "original_filename": original_filename,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "device_serial": serial,
            "log_id": log_id,
            "session_index": session_index,
            "conversion_status": status,
            "channels": channels,
            "file_size_bytes": file_size_bytes,
        }
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        self._load_index().add(hash_val)
        return entry


# --- Device Store ---

class DeviceStore:
    """Manages a single Zarr v3 store per device with indexed session groups."""

    def __init__(self, device_dir: Path):
        self.store_path = device_dir / "data.zarr"
        self._root = None

    def open(self, mode: str = "a"):
        """Open the Zarr store. Creates if it doesn't exist."""
        import zarr
        self._root = zarr.open_group(str(self.store_path), mode=mode)
        # Initialize root attrs if new store
        if "session_count" not in self._root.attrs:
            self._root.attrs["zarr_format"] = 3
            self._root.attrs["session_count"] = 0
            self._root.attrs["sessions"] = {}
        return self._root

    @property
    def root(self):
        if self._root is None:
            self.open()
        return self._root

    def next_session_index(self) -> int:
        """Return the next available session index."""
        return int(self.root.attrs.get("session_count", 0))

    def add_session(self, index: int, attrs: Optional[dict] = None):
        """Create a new session group at the given index."""
        group = self.root.require_group(str(index))
        if attrs:
            group.attrs.update(attrs)
        return group

    def update_sessions_index(self, index: int, summary: dict):
        """Update the root sessions mapping with a new session summary.

        Summary should include: start_utc, start_utc_us, end_utc, end_utc_us,
        duration_seconds, channels (dict with rates).
        """
        sessions = dict(self.root.attrs.get("sessions", {}))
        sessions[str(index)] = summary
        self.root.attrs["sessions"] = sessions
        self.root.attrs["session_count"] = len(sessions)

    def get_sessions_index(self) -> dict:
        """Return the root sessions mapping."""
        return dict(self.root.attrs.get("sessions", {}))

    def open_stream_session(self):
        """Create a new stream sub-group under stream/ with identical layout.

        Returns (group, index) tuple.
        """
        stream_parent = self.root.require_group("stream")
        if "trust_level" not in stream_parent.attrs:
            stream_parent.attrs["trust_level"] = "low"
            stream_parent.attrs["note"] = "Live BLE stream — may have packet loss"
            stream_parent.attrs["sessions"] = {}
            stream_parent.attrs["session_count"] = 0

        # Find next stream index
        idx = int(stream_parent.attrs.get("session_count", 0))
        group = stream_parent.require_group(str(idx))
        stream_parent.attrs["session_count"] = idx + 1
        return group, idx

    def update_stream_index(self, index: int, summary: dict):
        """Update the stream sessions mapping."""
        stream_parent = self.root.require_group("stream")
        sessions = dict(stream_parent.attrs.get("sessions", {}))
        sessions[str(index)] = summary
        stream_parent.attrs["sessions"] = sessions

    def close(self):
        """Close the store (zarr handles this via GC, but explicit is better)."""
        self._root = None
