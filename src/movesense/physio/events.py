"""Event model and persistence for physiological event annotations."""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class Event:
    """A labeled physiological event."""
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    timestamp_s: float = 0.0
    duration_s: float = 0.0  # 0 = point event
    event_type: str = ""
    confidence: float = 1.0
    source_channels: list[str] = field(default_factory=list)
    description: str = ""
    is_manual: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class EventStore:
    """Manages events for a session, persisted as events.json."""

    def __init__(self, session_dir: Path | str):
        self.session_dir = Path(session_dir)
        self.events_file = self.session_dir / "events.json"
        self.events: list[Event] = []
        self._load()

    def _load(self) -> None:
        """Load events from JSON file if it exists."""
        if self.events_file.exists():
            try:
                data = json.loads(self.events_file.read_text())
                self.events = [Event.from_dict(e) for e in data.get("events", [])]
                log.info(f"Loaded {len(self.events)} events from {self.events_file}")
            except Exception as e:
                log.warning(f"Failed to load events from {self.events_file}: {e}")
                self.events = []
        else:
            self.events = []

    def save(self) -> None:
        """Persist events to JSON file."""
        data = {"events": [e.to_dict() for e in self.events]}
        self.events_file.write_text(json.dumps(data, indent=2))

    def add(self, event: Event) -> Event:
        """Add an event and save."""
        self.events.append(event)
        self.save()
        return event

    def add_many(self, events: list[Event]) -> int:
        """Add multiple events and save once."""
        self.events.extend(events)
        self.save()
        return len(events)

    def get(self, event_id: str) -> Optional[Event]:
        """Get an event by ID."""
        return next((e for e in self.events if e.id == event_id), None)

    def update(self, event_id: str, updates: dict) -> Optional[Event]:
        """Update an event's fields."""
        event = self.get(event_id)
        if event is None:
            return None
        for key, value in updates.items():
            if hasattr(event, key) and key != "id":
                setattr(event, key, value)
        self.save()
        return event

    def delete(self, event_id: str) -> bool:
        """Delete an event by ID."""
        before = len(self.events)
        self.events = [e for e in self.events if e.id != event_id]
        if len(self.events) < before:
            self.save()
            return True
        return False

    def filter(self, event_type: Optional[str] = None, min_confidence: float = 0.0,
               is_manual: Optional[bool] = None) -> list[Event]:
        """Filter events by criteria."""
        result = self.events
        if event_type:
            result = [e for e in result if e.event_type == event_type]
        if min_confidence > 0:
            result = [e for e in result if e.confidence >= min_confidence]
        if is_manual is not None:
            result = [e for e in result if e.is_manual == is_manual]
        return result

    def to_csv(self) -> str:
        """Export events as CSV string."""
        lines = ["id,timestamp_s,duration_s,event_type,confidence,source_channels,description,is_manual"]
        for e in self.events:
            channels = ";".join(e.source_channels)
            desc = e.description.replace(",", ";")
            lines.append(f"{e.id},{e.timestamp_s},{e.duration_s},{e.event_type},{e.confidence},{channels},{desc},{e.is_manual}")
        return "\n".join(lines)

    def clear(self) -> None:
        """Remove all events."""
        self.events = []
        self.save()
