"""Encostay supply collector — map-view estimate via Playwright.

Extracts '검색 결과 N개의 하우스' from the Encostay map-search page.

This is a map-view estimate, not an exact total from the database.
The same URL, viewport (1920×1080), and zoom are held constant so that
day-over-day counts are comparable trends rather than absolute totals.

Returns dict: {count, raw_count_text, status, error}.

Snapshots saved to: data/snapshots/supply/encostay__{region}__{timestamp}.html
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

_SNAPSHOT_DIR = Path(__file__).parents[3] / "data" / "snapshots" / "supply"
_PAGE_LOAD_TIMEOUT = 30_000
_COUNT_WAIT_TIMEOUT = 20_000
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HOUSE_COUNT_PATTERN = re.compile(
    r"검색\s*결과\s+([0-9][0-9,]*)\s*개의\s*하우스",
)


def _snapshot_path(region_key: str) -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _SNAPSHOT_DIR / f"encostay__{region_key}__{ts}.html"


def _parse_house_count(text: str) -> Tuple[Optional[int], str]:
    """Return (count, raw_matched_snippet) for '검색 결과 N개의 하우스'."""
    m = _HOUSE_COUNT_PATTERN.search(text)
    if m:
        try:
            return int(m.group(1).replace(",", "")), m.group(0)
        except ValueError:
            pass
    return None, ""


async def _collect(
    region_key: str,
    region_label: str,
    url: str,
    save_snapshot: bool,
) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            locale="ko-KR",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        try:
            print(f"  [CHECKED] encostay / {region_label} — {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
            await page.wait_for_timeout(3_000)

            await page.evaluate("document.body.style.zoom = '30%'")
            print(f"  [DEBUG] encostay zoom_applied=30%")
            await page.wait_for_timeout(3_000)

            try:
                await page.wait_for_function(
                    "() => document.body.innerText.includes('개의 하우스')",
                    timeout=_COUNT_WAIT_TIMEOUT,
                )
                print(f"  [DEBUG] encostay '개의 하우스' text appeared")
            except PlaywrightTimeoutError:
                print(
                    f"  [DEBUG] encostay wait_for_function timed out — proceeding with current page text",
                    file=sys.stderr,
                )

            if save_snapshot:
                snap = _snapshot_path(region_key)
                snap.write_text(await page.content(), encoding="utf-8")
                print(f"  [SNAPSHOT] {snap.name}")

            visible_text = (await page.evaluate("document.body.innerText") or "").strip()
            print(f"  [DEBUG] encostay visible_text_len={len(visible_text)}")

            count, raw_text = _parse_house_count(visible_text[:5000])
            if count is not None:
                print(f"  [COLLECTED] encostay / {region_label}: {count} raw='{raw_text}'")
                return {"count": count, "raw_count_text": raw_text, "status": "ok", "error": None}

            text_len = len(visible_text)
            if text_len < 300:
                print(
                    f"  [NO_RESULTS] encostay / {region_label}: page appears empty (len={text_len})",
                    file=sys.stderr,
                )
                return {"count": None, "raw_count_text": "", "status": "count_not_found", "error": "page_appears_empty"}

            print(
                f"  [NO_RESULTS] encostay / {region_label}: '검색 결과 N개의 하우스' not found (text_len={text_len})",
                file=sys.stderr,
            )
            return {"count": None, "raw_count_text": "", "status": "count_not_found", "error": "encostay_house_count_not_found"}

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"  [FAILED] encostay / {region_label}: {msg}", file=sys.stderr)
            if save_snapshot:
                try:
                    snap = _snapshot_path(region_key)
                    snap.write_text(await page.content(), encoding="utf-8")
                    print(f"  [SNAPSHOT] {snap.name} (on error)")
                except Exception:
                    pass
            return {"count": None, "raw_count_text": "", "status": "failed", "error": msg}
        finally:
            await browser.close()


def collect_sync(
    competitor_key: str,
    region_key: str,
    region_label: str,
    url: str,
    save_snapshot: bool = True,
) -> dict:
    """Return dict: {count, raw_count_text, status, error}."""
    return asyncio.run(_collect(region_key, region_label, url, save_snapshot))
