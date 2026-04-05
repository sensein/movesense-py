"""Tests for Event model and EventStore."""

import json
from pathlib import Path

import pytest
from movensense.physio.events import Event, EventStore


class TestEventModel:
    def test_create_event(self):
        e = Event(timestamp_s=1.5, event_type="r_peak", confidence=0.95)
        assert e.timestamp_s == 1.5
        assert e.id.startswith("evt_")

    def test_serialize_deserialize(self):
        e = Event(timestamp_s=2.0, duration_s=0.5, event_type="artifact", source_channels=["ECG", "ACC"])
        d = e.to_dict()
        e2 = Event.from_dict(d)
        assert e2.timestamp_s == 2.0
        assert e2.source_channels == ["ECG", "ACC"]


class TestEventStore:
    def test_save_and_load(self, tmp_path):
        store = EventStore(tmp_path)
        store.add(Event(timestamp_s=1.0, event_type="test"))
        store.add(Event(timestamp_s=2.0, event_type="test2"))
        assert len(store.events) == 2

        # Reload
        store2 = EventStore(tmp_path)
        assert len(store2.events) == 2

    def test_filter_by_type(self, tmp_path):
        store = EventStore(tmp_path)
        store.add(Event(event_type="r_peak"))
        store.add(Event(event_type="artifact"))
        store.add(Event(event_type="r_peak"))
        assert len(store.filter(event_type="r_peak")) == 2

    def test_filter_by_confidence(self, tmp_path):
        store = EventStore(tmp_path)
        store.add(Event(confidence=0.9))
        store.add(Event(confidence=0.3))
        assert len(store.filter(min_confidence=0.5)) == 1

    def test_delete_event(self, tmp_path):
        store = EventStore(tmp_path)
        e = store.add(Event(event_type="test"))
        assert store.delete(e.id)
        assert len(store.events) == 0

    def test_update_event(self, tmp_path):
        store = EventStore(tmp_path)
        e = store.add(Event(event_type="test", description="old"))
        store.update(e.id, {"description": "new"})
        assert store.get(e.id).description == "new"

    def test_csv_export(self, tmp_path):
        store = EventStore(tmp_path)
        store.add(Event(timestamp_s=1.0, event_type="r_peak"))
        csv = store.to_csv()
        assert "r_peak" in csv
        assert "timestamp_s" in csv

    def test_empty_store(self, tmp_path):
        store = EventStore(tmp_path)
        assert len(store.events) == 0
        assert store.get("nonexistent") is None
