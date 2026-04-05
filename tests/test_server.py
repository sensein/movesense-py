"""Tests for API endpoints."""

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from movensense.server.app import create_app
from movensense.server.auth import set_active_token


@pytest.fixture
def client(fake_data_dir):
    """Create a test client with fake data and a known token."""
    with patch("movensense.server.auth.get_or_create_token", return_value="testtoken123"):
        app = create_app(fake_data_dir)
    set_active_token("testtoken123")
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer testtoken123"}


@pytest.fixture
def corrupted_client(corrupted_data_dir):
    with patch("movensense.server.auth.get_or_create_token", return_value="testtoken123"):
        app = create_app(corrupted_data_dir)
    set_active_token("testtoken123")
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_no_auth(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_accessible_without_token(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200


class TestAuthMiddleware:
    def test_401_without_token(self, client):
        resp = client.get("/api/devices")
        assert resp.status_code in (401, 403)

    def test_401_with_invalid_token(self, client):
        resp = client.get("/api/devices", headers={"Authorization": "Bearer wrongtoken"})
        assert resp.status_code == 401

    def test_200_with_valid_token(self, client, auth_headers):
        resp = client.get("/api/devices", headers=auth_headers)
        assert resp.status_code == 200


class TestDevicesEndpoint:
    def test_list_devices(self, client, auth_headers):
        resp = client.get("/api/devices", headers=auth_headers)
        assert resp.status_code == 200
        devices = resp.json()["devices"]
        serials = [d["serial"] for d in devices]
        assert "000000000000" in serials
        assert "000000000001" in serials

    def test_device_date_count(self, client, auth_headers):
        resp = client.get("/api/devices", headers=auth_headers)
        d = next(d for d in resp.json()["devices"] if d["serial"] == "000000000000")
        assert d["date_count"] == 1


class TestDatesEndpoint:
    def test_list_dates(self, client, auth_headers):
        resp = client.get("/api/devices/000000000000/dates", headers=auth_headers)
        assert resp.status_code == 200
        assert "2026-04-04" in resp.json()["dates"]

    def test_404_nonexistent_device(self, client, auth_headers):
        resp = client.get("/api/devices/NONEXISTENT/dates", headers=auth_headers)
        assert resp.status_code == 404


class TestSessionsEndpoint:
    def test_list_sessions(self, client, auth_headers):
        resp = client.get("/api/devices/000000000000/dates/2026-04-04/sessions", headers=auth_headers)
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) >= 1
        assert sessions[0]["log_id"] == 1
        assert "MeasECGmV" in sessions[0]["channels"]


class TestChannelsEndpoint:
    def test_list_channels(self, client, auth_headers):
        resp = client.get("/api/devices/000000000000/dates/2026-04-04/sessions/1/channels", headers=auth_headers)
        assert resp.status_code == 200
        channels = resp.json()["channels"]
        names = [c["name"] for c in channels]
        assert "MeasECGmV" in names

    def test_channel_metadata(self, client, auth_headers):
        resp = client.get("/api/devices/000000000000/dates/2026-04-04/sessions/1/channels", headers=auth_headers)
        ecg = next(c for c in resp.json()["channels"] if c["name"] == "MeasECGmV")
        assert ecg["sampling_rate_hz"] == 200.0
        assert ecg["unit"] == "mV"
        assert ecg["sample_count"] == 500


class TestChannelDataEndpoint:
    def test_get_data(self, client, auth_headers):
        resp = client.get(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/channels/MeasECGmV/data",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_samples"] == 500
        assert len(body["data"]) == 500

    def test_pagination(self, client, auth_headers):
        resp = client.get(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/channels/MeasECGmV/data?offset=10&limit=5",
            headers=auth_headers,
        )
        body = resp.json()
        assert len(body["data"]) == 5
        assert body["offset"] == 10
        assert body["total_samples"] == 500

    def test_multi_column_data(self, client, auth_headers):
        resp = client.get(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/channels/MeasAcc/data",
            headers=auth_headers,
        )
        body = resp.json()
        assert len(body["data"]) == 100
        assert len(body["data"][0]) == 3

    def test_404_nonexistent_channel(self, client, auth_headers):
        resp = client.get(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/channels/FAKE/data",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestSessionMetadataEndpoint:
    def test_get_metadata(self, client, auth_headers):
        resp = client.get(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/metadata",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["device_serial"] == "000000000000"
        assert "MeasECGmV" in body["measurement_paths"]


class TestRefreshEndpoint:
    def test_refresh(self, client, auth_headers):
        resp = client.post("/api/refresh", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "refreshed"
        assert body["devices"] >= 1


class TestWindowStatsEndpoint:
    def test_window_stats(self, client, auth_headers):
        resp = client.post(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/window-stats?start=0",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "channels" in body
        assert "MeasECGmV" in body["channels"]
        ecg = body["channels"]["MeasECGmV"]
        assert "min" in ecg
        assert "max" in ecg
        assert "mean" in ecg

    def test_window_stats_with_range(self, client, auth_headers):
        resp = client.post(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/window-stats?start=0&end=1.0",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_window_stats_404(self, client, auth_headers):
        resp = client.post(
            "/api/devices/NONEXISTENT/dates/2026-04-04/sessions/1/window-stats",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestDownsampleEndpoint:
    def test_downsample(self, client, auth_headers):
        resp = client.get(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/channels/MeasECGmV/downsample?buckets=10",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["channel"] == "MeasECGmV"
        assert body["buckets"] == 10
        assert len(body["data"]["min"]) == 10

    def test_downsample_404(self, client, auth_headers):
        resp = client.get(
            "/api/devices/000000000000/dates/2026-04-04/sessions/1/channels/FAKE/downsample",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestCoverageEndpoint:
    def test_get_coverage(self, client, auth_headers):
        resp = client.get("/api/devices/000000000000/coverage/2026/4", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["year"] == 2026
        assert body["month"] == 4
        assert len(body["days"]) == 1
        assert body["days"][0]["date"] == "2026-04-04"
        assert body["summary"]["days_with_data"] == 1

    def test_coverage_empty_month(self, client, auth_headers):
        resp = client.get("/api/devices/000000000000/coverage/2026/1", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["days"] == []

    def test_coverage_404_device(self, client, auth_headers):
        resp = client.get("/api/devices/NONEXISTENT/coverage/2026/4", headers=auth_headers)
        assert resp.status_code == 404


class TestConcurrentAccess:
    def test_concurrent_requests(self, client, auth_headers):
        """T015a: 10 concurrent requests should all succeed."""
        import concurrent.futures

        def make_request():
            return client.get("/api/devices", headers=auth_headers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(make_request) for _ in range(10)]
            results = [f.result() for f in futures]

        assert all(r.status_code == 200 for r in results)


class TestCorruptedZarr:
    def test_corrupted_zarr_doesnt_crash(self, corrupted_client, auth_headers={"Authorization": "Bearer testtoken123"}):
        """T015b: API should return valid JSON even with corrupted stores."""
        resp = corrupted_client.get("/api/devices", headers=auth_headers)
        assert resp.status_code == 200
