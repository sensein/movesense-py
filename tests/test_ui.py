"""Browser UI tests using Playwright.

Requires a running server: `movensense serve`
Run with: pytest tests/test_ui.py -v --headed (to see browser)
"""

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from playwright.sync_api import Page, expect
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# Skip all tests if playwright not available
pytestmark = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")


@pytest.fixture(scope="module")
def server(fake_data_dir_module):
    """Start a test server with fake data on a random port."""
    import socket
    import threading
    import uvicorn

    from movensense.server.app import create_app
    from movensense.server.auth import set_active_token

    # Find free port
    with socket.socket() as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    with patch("movensense.server.auth.get_or_create_token", return_value="testtoken"):
        app = create_app(fake_data_dir_module)
    set_active_token("testtoken")

    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "127.0.0.1", "port": port, "log_level": "error"},
        daemon=True,
    )
    thread.start()
    time.sleep(1)  # wait for server startup

    yield f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def fake_data_dir_module(tmp_path_factory):
    """Module-scoped fake data dir (shared across all UI tests)."""
    import numpy as np
    import zarr

    tmp_path = tmp_path_factory.mktemp("data")
    serial = "000000000000"
    date = "2026-04-04"
    session_dir = tmp_path / serial / date
    session_dir.mkdir(parents=True)

    zarr_path = session_dir / "Movesense_log_1_000000000000.zarr"
    store = zarr.open_group(str(zarr_path), mode="w")
    store.attrs["device_serial"] = serial
    store.attrs["fetch_date"] = "2026-04-04T19:20:00Z"
    store.attrs["measurement_paths"] = ["MeasECGmV", "MeasAcc"]
    store.attrs["utc_time"] = 1712000000000000

    # ECG: simulate ~10s of data with R-peaks
    fs_ecg = 200
    t = np.arange(0, 10, 1 / fs_ecg)
    ecg = np.random.randn(len(t)) * 0.05
    for peak_time in np.arange(0.5, 10, 0.85):
        idx = int(peak_time * fs_ecg)
        if idx < len(ecg) - 2:
            ecg[idx] = 1.0
            ecg[idx - 1] = -0.2
            ecg[idx + 1] = -0.3

    grp = store.create_group("MeasECGmV")
    grp.create_array("data", data=ecg.astype(np.float32))
    grp.attrs["sensor_type"] = "ECG"
    grp.attrs["sampling_rate_hz"] = float(fs_ecg)
    grp.attrs["unit"] = "mV"

    # ACC
    acc = np.column_stack([
        np.random.randn(520) * 0.1,
        np.random.randn(520) * 0.1,
        np.ones(520) + np.random.randn(520) * 0.05,
    ])
    grp2 = store.create_group("MeasAcc")
    grp2.create_array("data", data=acc.astype(np.float32))
    grp2.attrs["sensor_type"] = "MeasAcc"
    grp2.attrs["sampling_rate_hz"] = 52.0
    grp2.attrs["shape_description"] = "Nx3 (x, y, z)"

    (session_dir / "Movesense_log_1_000000000000.csv").write_text("t,v\n0,0\n")
    (session_dir / "Movesense_log_1_000000000000.json").write_text("{}")

    return tmp_path


class TestDataBrowser:
    def test_loads_device_list(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.wait_for_selector("text=000000000000", timeout=5000)
        assert page.locator("text=000000000000").is_visible()

    def test_navigate_to_dates(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=000000000000")
        page.wait_for_selector("text=2026-04-04", timeout=5000)
        assert page.locator("text=2026-04-04").is_visible()

    def test_navigate_to_sessions(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=000000000000")
        page.click("text=2026-04-04")
        page.wait_for_selector("text=Log 1", timeout=5000)
        assert page.locator("text=Log 1").is_visible()

    def test_session_shows_channel_viewer(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=000000000000")
        page.click("text=2026-04-04")
        page.click("text=Log 1")
        # Wait for channel selector to appear
        page.wait_for_selector(".cv-sidebar", timeout=10000)
        assert page.locator(".cv-sidebar").locator("text=MeasECGmV").first.is_visible()
        assert page.locator(".cv-sidebar").locator("text=MeasAcc").first.is_visible()

    def test_charts_render(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=000000000000")
        page.click("text=2026-04-04")
        page.click("text=Log 1")
        # Wait for uPlot canvases to appear
        page.wait_for_selector("canvas", timeout=10000)
        canvases = page.locator("canvas").count()
        assert canvases >= 2  # at least ECG + ACC charts

    def test_stats_panel_shows_hr(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=000000000000")
        page.click("text=2026-04-04")
        page.click("text=Log 1")
        # Wait for stats to compute
        page.wait_for_selector("text=bpm", timeout=15000)
        assert page.locator("text=bpm").is_visible()

    def test_channel_toggle_hides_chart(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=000000000000")
        page.click("text=2026-04-04")
        page.click("text=Log 1")
        page.wait_for_selector("canvas", timeout=10000)
        initial_canvases = page.locator("canvas").count()

        # Uncheck MeasAcc
        page.locator(".cv-sidebar").locator("text=MeasAcc").locator("input").uncheck()
        page.wait_for_timeout(1000)
        assert page.locator("canvas").count() < initial_canvases


class TestCalendar:
    def test_calendar_tab_loads(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=Calendar")
        page.wait_for_selector("#tab-calendar", timeout=5000)
        assert page.locator("#tab-calendar").is_visible()

    def test_calendar_shows_device_selector(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=Calendar")
        page.wait_for_selector("#cal-device", timeout=5000)
        assert page.locator("#cal-device").is_visible()


class TestAuth:
    def test_no_token_shows_error(self, page: Page, server):
        page.goto(server)
        page.wait_for_selector("text=No token", timeout=5000)
        assert page.locator("text=No token").is_visible()

    def test_wrong_token_shows_error(self, page: Page, server):
        page.goto(f"{server}/?token=wrongtoken")
        page.wait_for_selector("text=Authentication", timeout=5000)


class TestScreenshots:
    """Visual regression tests — capture screenshots for manual review."""

    def test_screenshot_device_list(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.wait_for_selector("text=000000000000", timeout=5000)
        page.screenshot(path="screenshots/device_list.png", full_page=True)

    def test_screenshot_channel_viewer(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=000000000000")
        page.click("text=2026-04-04")
        page.click("text=Log 1")
        page.wait_for_selector("canvas", timeout=10000)
        page.wait_for_timeout(2000)  # let stats compute
        page.screenshot(path="screenshots/channel_viewer.png", full_page=True)

    def test_screenshot_calendar(self, page: Page, server):
        page.goto(f"{server}/?token=testtoken")
        page.click("text=Calendar")
        page.wait_for_timeout(2000)
        page.screenshot(path="screenshots/calendar.png", full_page=True)
