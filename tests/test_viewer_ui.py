"""Playwright tests for the server-driven streaming viewer UI."""

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

STATIC_DIR = Path(__file__).parent.parent / "src" / "movesense" / "server" / "static"


@pytest.fixture(scope="module")
def server():
    """Start the movesense server for UI testing."""
    from movesense.server.app import create_app
    import uvicorn
    import threading

    data_dir = Path.home() / "dbp" / "data" / "movesense"
    if not data_dir.exists():
        pytest.skip("No test data available")

    app = create_app(data_dir)
    token = app.state.token

    server_thread = threading.Thread(
        target=uvicorn.run, args=(app,),
        kwargs={"host": "127.0.0.1", "port": 8599, "log_level": "error"},
        daemon=True,
    )
    server_thread.start()
    time.sleep(2)

    yield {"url": f"http://127.0.0.1:8599/?token={token}", "token": token}


@pytest.fixture
def page_with_errors(server):
    """Playwright page that collects JS errors."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        pg.on("pageerror", lambda err: errors.append(str(err)))
        pg.goto(server["url"])
        pg.wait_for_timeout(2000)
        yield pg, errors
        browser.close()


class TestViewerRendering:
    def test_no_js_errors_on_load(self, page_with_errors):
        page, errors = page_with_errors
        assert len(errors) == 0, f"JS errors on page load: {errors}"

    def test_device_list_shows(self, page_with_errors):
        page, errors = page_with_errors
        buttons = page.query_selector_all(".device-btn")
        assert len(buttons) >= 1, "No device buttons found"

    def test_no_js_errors_on_device_select(self, page_with_errors):
        page, errors = page_with_errors
        page.click(".device-btn")
        page.wait_for_timeout(8000)
        assert len(errors) == 0, f"JS errors after device select: {errors}"

    def test_charts_render(self, page_with_errors):
        page, errors = page_with_errors
        page.click(".device-btn")
        page.wait_for_timeout(8000)
        # ECharts renders to canvas
        canvases = page.query_selector_all("canvas")
        assert len(canvases) >= 1, "No chart canvases found"

    def test_channel_checkboxes_present(self, page_with_errors):
        page, errors = page_with_errors
        page.click(".device-btn")
        page.wait_for_timeout(8000)
        checkboxes = page.query_selector_all("#controls input[type=checkbox]")
        assert len(checkboxes) >= 1, "No channel checkboxes found"

    def test_no_js_errors_on_zoom(self, page_with_errors):
        page, errors = page_with_errors
        page.click(".device-btn")
        page.wait_for_timeout(8000)
        # Scroll wheel to zoom
        charts = page.query_selector("#charts")
        if charts:
            box = charts.bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.wheel(0, -300)  # zoom in
            page.wait_for_timeout(3000)
        assert len(errors) == 0, f"JS errors after zoom: {errors}"
