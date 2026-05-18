"""LiveAnywhere supply/listing count collector using Playwright.

Extraction strategy (in order):
1. Primary: search visible page text for '검색결과 N건' (canonical counter on SRP).
2. Secondary: CSS selectors for a dedicated count element.
3. Tertiary: generic Korean/English count patterns in page text.
4. Quaternary: count visible listing cards as a lower-bound fallback.

Returns dict: {count, raw_count_text, status, error}.

Snapshots saved to: data/snapshots/supply/liveanywhere__{region}__{timestamp}.html
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

_SNAPSHOT_DIR = Path(__file__).parents[3] / "data" / "snapshots" / "supply"
_PAGE_LOAD_TIMEOUT = 25_000
_CONTENT_TIMEOUT = 12_000
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_COUNT_SELECTORS = [
    ".search-count",
    ".results-count",
    ".listing-count",
    ".total-count",
    "[class*='result'][class*='count']",
    "[class*='search'][class*='count']",
    "[data-testid='result-count']",
    "[data-testid='search-count']",
]

_CARD_SELECTORS = [
    ".room-card",
    ".listing-card",
    ".property-card",
    ".space-card",
    "[class*='room-card']",
    "[class*='listing-card']",
    "[class*='property-card']",
    "article",
]

# Ordered: canonical Korean SRP counter first, generic fallbacks after.
_COUNT_PATTERNS = [
    r"검색결과\s+([0-9][0-9,]*)\s*건",            # 검색결과 47건
    r"검색\s*결과\s+([0-9][0-9,]*)\s*건",          # 검색 결과 47건
    r"([0-9][0-9,]*)\s*건(?:\s*의\s*검색결과)?",   # 47건의 검색결과
    r"([0-9][0-9,]*)\s*(?:rooms?|listings?|spaces?|stays?|properties|결과|개)",
    r"([0-9][0-9,]*)\s+(?:results?|places?)",
    r"showing\s+([0-9][0-9,]*)",
]


def _snapshot_path(region_key: str) -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _SNAPSHOT_DIR / f"liveanywhere__{region_key}__{ts}.html"


def _parse_count_with_text(text: str) -> Tuple[Optional[int], str]:
    """Return (count, raw_matched_snippet). Returns (None, '') on no match."""
    for pattern in _COUNT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", "")), m.group(0)
            except ValueError:
                continue
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
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        try:
            print(f"  [CHECKED] liveanywhere / {region_label} — {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
            await page.wait_for_timeout(2_500)

            card_sel_matched = None
            for sel in _CARD_SELECTORS:
                try:
                    await page.wait_for_selector(sel, timeout=_CONTENT_TIMEOUT)
                    card_sel_matched = sel
                    break
                except PlaywrightTimeoutError:
                    continue

            if save_snapshot:
                snap = _snapshot_path(region_key)
                snap.write_text(await page.content(), encoding="utf-8")
                print(f"  [SNAPSHOT] {snap.name}")

            visible_text = (await page.evaluate("document.body.innerText") or "").strip()

            # Strategy 1: page text (Korean SRP counter pattern first)
            count, raw_text = _parse_count_with_text(visible_text[:5000])
            if count is not None:
                print(f"  [COLLECTED] liveanywhere / {region_label}: {count} raw='{raw_text}'")
                return {"count": count, "raw_count_text": raw_text, "status": "ok", "error": None}

            # Strategy 2: dedicated count element
            for sel in _COUNT_SELECTORS:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    count, raw_text = _parse_count_with_text(text)
                    if count is not None:
                        print(f"  [COLLECTED] liveanywhere / {region_label}: {count} (from '{sel}')")
                        return {"count": count, "raw_count_text": raw_text, "status": "ok", "error": None}

            # Strategy 3: visible card count as lower-bound
            if card_sel_matched:
                cards = await page.query_selector_all(card_sel_matched)
                if cards:
                    n = len(cards)
                    print(f"  [COLLECTED] liveanywhere / {region_label}: {n} (visible card count)")
                    return {
                        "count": n,
                        "raw_count_text": f"{n} visible cards",
                        "status": "ok",
                        "error": "count_from_visible_cards_only",
                    }

            text_len = len(visible_text)
            if text_len < 300:
                print(f"  [NO_RESULTS] liveanywhere / {region_label}: page appears empty (len={text_len})", file=sys.stderr)
                return {"count": None, "raw_count_text": "", "status": "count_not_found", "error": "page_appears_empty"}

            print(f"  [NO_RESULTS] liveanywhere / {region_label}: no count found (len={text_len})", file=sys.stderr)
            return {"count": None, "raw_count_text": "", "status": "count_not_found", "error": "no_count_found"}

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"  [FAILED] liveanywhere / {region_label}: {msg}", file=sys.stderr)
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
