"""Capture README screenshots from a running NPlayStudio instance.

Uses the system Chromium via Playwright so the theme and the cookie banner can
be controlled: the app reads both from localStorage, and a default headless run
shows the light theme with the consent dialog over the page.

  python3 scripts/capture_screenshots.py [base_url]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:12000"
# GitHub renders README images at roughly 880px, so 1x on a 1280px viewport is
# already sharper than needed and keeps the committed files small.
SCALE = 1
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"

PAGES = [
    ("home", "/", 1280, 1150),
    ("video-compress", "/video-compress.html", 1280, 1000),
    ("video-merge", "/video-merge.html", 1280, 1000),
    ("video-to-gif", "/video-to-gif.html", 1280, 1000),
    ("audio-extract", "/audio-extract.html", 1280, 1000),
    ("image-crop", "/image-crop.html", 1280, 1000),
    ("faq", "/faq.html", 1280, 1100),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/chromium",
            args=["--no-sandbox", "--disable-gpu", "--hide-scrollbars"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 1000},
            device_scale_factor=SCALE,
            color_scheme="dark",
        )
        # Dark theme, consent already answered, so no banner covers the UI.
        context.add_init_script(
            "localStorage.setItem('nplay.theme', 'dark');"
            "localStorage.setItem('nplay.consent', 'accepted');"
        )
        page = context.new_page()
        for name, path, width, height in PAGES:
            page.set_viewport_size({"width": width, "height": height})
            page.goto(BASE + path, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1200)
            target = OUT / f"{name}.png"
            page.screenshot(path=str(target))
            print(f"  {name + '.png':<24} {target.stat().st_size:>8} bytes")
        browser.close()
    print(f"Done -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
