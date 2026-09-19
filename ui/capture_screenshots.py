# -*- coding: utf-8 -*-
"""Capture the status page screenshot after Streamlit has fully rendered (streamlit run ui/app.py must be running).
Browser: playwright's bundled Chromium, or the executable named by env WELDSIM_BROWSER.
Page: env WELDSIM_UI_URL (default http://localhost:8501)."""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
CASES = ("example_tube_synthetic",)


def main():
    out = HERE / "screenshots"
    out.mkdir(exist_ok=True)
    exe = os.environ.get("WELDSIM_BROWSER") or None
    url = os.environ.get("WELDSIM_UI_URL", "http://localhost:8501").rstrip("/")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1500}, device_scale_factor=1)
        for case_name in CASES:
            page.goto(url + "/?case=" + case_name, wait_until="networkidle")
            page.get_by_text("06 · Selfcheck", exact=True).wait_for(state="visible", timeout=30000)
            page.mouse.wheel(0, 3000)                                  # Streamlit scrolls inside its own container
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / (case_name + ".png")), full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
