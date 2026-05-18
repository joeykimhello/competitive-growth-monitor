"""Google Ads Transparency Center collector — counts by platform.

Navigation flow:
1. Load https://adstransparency.google.com/?region=KR
2. Try each search term in order; stop on first term that reaches a gallery page.
   If preferred_advertiser_name is set, prefer an exact-match suggestion over the first one.
3. Handle disambiguation panel if shown.
4. Reach /advertiser/{ID}?region=KR gallery page.
5. Extract total count; navigate per-platform URL and extract each count.

Per-platform counts are read by appending &platform=SEARCH|YOUTUBE|MAPS|PLAY|SHOPPING
to the gallery URL and reading the count from the first creative link aria-label:
    광고(N개 중 M번째)  →  N = total ads on this platform

Status values:
  ok                    gallery reached, ≥1 count extracted
  partial               gallery reached, total got but some platforms missing
  advertiser_not_found  all search terms exhausted, no gallery reached
  no_ads                gallery reached but zero ads detected
  count_not_found       gallery reached, count extraction failed (not zero)
  failed                unexpected exception or search input not found

Log labels:
  [search_input_not_found]    search input element missing
  [advertiser_not_found]      all terms tried, no gallery reached
  [gallery_not_reached]       individual term failed to reach gallery
  [DISAMBIG]                  multiple-advertisers panel → clicked first
  [GALLERY]                   reached gallery page
  [count_not_found]           count could not be extracted
  [no_ads]                    zero ads detected on gallery page
  [platform_filter_not_found] platform URL navigation exception
  [GOOGLE_TC]                 counts extracted successfully
  [FAILED]                    unexpected exception
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright

_SNAPSHOT_DIR = Path(__file__).parents[3] / "data" / "snapshots" / "ads"
_PAGE_LOAD_TIMEOUT = 30_000
_INTERACTION_TIMEOUT = 8_000
_BASE_URL = "https://adstransparency.google.com/?region=KR"

# Per-platform URL parameter → our result key
_PLATFORMS = {
    "google_search": "SEARCH",
    "youtube": "YOUTUBE",
    "google_maps": "MAPS",
    "google_play": "PLAY",
    "google_shopping": "SHOPPING",
}

_DISAMBIG_SELECTORS = [
    ".multiple-advertisers-panel .advertiser-button",
    ".advertisers-list .advertiser-button",
]

# Selectors for autocomplete suggestion items (advertiser, not domain)
_SUGGESTION_ITEM_SELECTORS = [
    ".search-suggestions-select:not(.search-suggestions-select-websites) material-select-item",
    "material-select.search-suggestions-select:first-of-type material-select-item",
    ".search-suggestions-select material-select-item",
]


def _snapshot_path(competitor: str, stage: str = "") -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"__{stage}" if stage else ""
    return _SNAPSHOT_DIR / f"{competitor}__google_tc{suffix}__{ts}.html"


async def _save_snapshot(page: Page, competitor: str, stage: str, enabled: bool) -> Optional[str]:
    if not enabled:
        return None
    snap = _snapshot_path(competitor, stage)
    try:
        snap.write_text(await page.content(), encoding="utf-8")
        return snap.name
    except Exception:
        return None


async def _type_and_search(page: Page, advertiser_name: str) -> bool:
    """Type advertiser_name into the search input. Returns False if input not found."""
    input_sel = "material-input input[type='text']"
    try:
        await page.wait_for_selector(input_sel, timeout=_INTERACTION_TIMEOUT)
    except PlaywrightTimeoutError:
        return False
    inp = await page.query_selector(input_sel)
    if not inp:
        return False
    await inp.click()
    # Clear existing text before typing
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Delete")
    await page.wait_for_timeout(200)
    await page.keyboard.type(advertiser_name, delay=60)
    await page.wait_for_timeout(1_200)
    return True


async def _wait_for_suggestions(page: Page) -> bool:
    """Wait for autocomplete suggestions to render."""
    for sel in [
        "material-select.search-suggestions-select material-select-item",
        ".search-suggestions-select material-select-item",
        ".search-suggestions-wrapper material-select-item",
        ".search-suggestions-wrapper",
    ]:
        try:
            await page.wait_for_selector(sel, timeout=_INTERACTION_TIMEOUT)
            return True
        except PlaywrightTimeoutError:
            continue
    return False


async def _get_suggestion_texts(page: Page) -> list[str]:
    """Return visible text of up to 5 suggestion items (for diagnostics)."""
    for sel in _SUGGESTION_ITEM_SELECTORS:
        items = await page.query_selector_all(sel)
        if items:
            texts = []
            for item in items[:5]:
                try:
                    t = (await item.inner_text()).strip()
                    if t:
                        texts.append(t[:80])
                except Exception:
                    pass
            if texts:
                return texts
    # Fallback: role-based
    try:
        options = page.get_by_role("option")
        cnt = await options.count()
        texts = []
        for i in range(min(cnt, 5)):
            try:
                t = (await options.nth(i).inner_text()).strip()
                if t:
                    texts.append(t[:80])
            except Exception:
                pass
        if texts:
            return texts
    except Exception:
        pass
    return []


async def _click_best_suggestion(page: Page, preferred_name: Optional[str]) -> str:
    """Click the best advertiser suggestion. Prefers exact match on preferred_name.

    Returns the text of the clicked item (empty string if nothing clicked).
    """
    for sel in _SUGGESTION_ITEM_SELECTORS:
        items = await page.query_selector_all(sel)
        if not items:
            continue

        # Try exact match first
        if preferred_name:
            preferred_lower = preferred_name.strip().lower()
            for item in items:
                try:
                    text = (await item.inner_text()).strip()
                    if text.lower() == preferred_lower:
                        await item.click()
                        return text
                except Exception:
                    pass

        # Fall back to first item
        try:
            text = (await items[0].inner_text()).strip()
            await items[0].click()
            return text
        except Exception:
            pass

    # Playwright role-based fallback
    try:
        options = page.get_by_role("option")
        cnt = await options.count()
        if cnt > 0:
            if preferred_name:
                preferred_lower = preferred_name.strip().lower()
                for i in range(min(cnt, 5)):
                    try:
                        text = (await options.nth(i).inner_text()).strip()
                        if text.lower() == preferred_lower:
                            await options.nth(i).click(timeout=3_000)
                            return text
                    except Exception:
                        pass
            text = (await options.first.inner_text()).strip()
            await options.first.click(timeout=3_000)
            return text
    except Exception:
        pass

    await page.keyboard.press("Enter")
    return ""


async def _handle_disambig(page: Page) -> bool:
    for sel in _DISAMBIG_SELECTORS:
        try:
            await page.wait_for_selector(sel, timeout=_INTERACTION_TIMEOUT)
            items = await page.query_selector_all(sel)
            if items:
                print("  [DISAMBIG] Multiple advertisers panel; clicking first match")
                await items[0].click()
                return True
        except PlaywrightTimeoutError:
            continue
    return False


async def _wait_for_gallery(page: Page) -> tuple:
    try:
        await page.wait_for_url("**/advertiser/**", timeout=_INTERACTION_TIMEOUT)
        return True, page.url
    except PlaywrightTimeoutError:
        pass
    current_url = page.url
    return ("/advertiser/" in current_url), current_url


async def _extract_count_from_gallery(page: Page) -> Optional[int]:
    """Extract ad count from creative link aria-label pattern 광고(N개 중 M번째)."""
    try:
        await page.wait_for_selector('a[aria-label*="광고("]', timeout=8_000)
    except PlaywrightTimeoutError:
        return None
    result = await page.evaluate(r"""
        () => {
            const links = document.querySelectorAll('a[aria-label]');
            for (const link of links) {
                const label = link.getAttribute('aria-label') || '';
                const m = label.match(/광고\((\d[\d,]*)개 중 \d+번째\)/);
                if (m) return m[1].replace(/,/g, '');
            }
            return null;
        }
    """)
    if result:
        try:
            return int(result)
        except (ValueError, TypeError):
            pass
    return None


async def _check_no_ads(page: Page) -> bool:
    """Return True if the gallery page indicates zero ads are available."""
    try:
        text = await page.evaluate("() => (document.body.innerText || '').toLowerCase()")
        indicators = ["광고가 없", "결과 없", "no ads", "0 ads", "광고를 찾을 수 없"]
        return any(ind in text for ind in indicators)
    except Exception:
        return False


async def _set_date_range_yesterday(page: Page, display_name: str) -> bool:
    """Set the gallery date range filter to '어제'. Returns True if successful."""
    # Open the date range control
    opened = False
    for sel in [
        "material-select[aria-label*='날짜']",
        "[aria-label*='날짜 범위']",
        ".date-range-select",
        "material-select.date-select",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.click()
                await page.wait_for_timeout(600)
                opened = True
                break
        except Exception:
            continue

    if not opened:
        for label in ["날짜 범위", "기간", "Date range"]:
            try:
                btn = page.get_by_role("button").filter(has_text=label)
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_timeout(600)
                    opened = True
                    break
            except Exception:
                continue

    if not opened:
        print(f"  [date_range] {display_name}: date range control not found")
        return False

    # Select "어제" from preset-container or material-select-item / role=option
    for option_text in ["어제", "Yesterday"]:
        # preset-container (Google Ads TC specific)
        try:
            preset = page.locator(".preset-container").get_by_text(option_text, exact=True)
            if await preset.count() > 0:
                await preset.first.click()
                await page.wait_for_timeout(500)
                # Click apply/confirm button if present
                for confirm_label in ["적용", "확인", "Apply", "OK"]:
                    try:
                        confirm = page.get_by_role("button").filter(has_text=confirm_label)
                        if await confirm.count() > 0:
                            await confirm.first.click()
                            await page.wait_for_timeout(2_000)
                            print(f"  [date_range] {display_name}: set to '어제' (confirmed)")
                            return True
                    except Exception:
                        continue
                await page.wait_for_timeout(1_500)
                print(f"  [date_range] {display_name}: set to '어제'")
                return True
        except Exception:
            pass

        # material-select-item fallback
        try:
            for item in await page.query_selector_all("material-select-item"):
                try:
                    if option_text in (await item.inner_text()).strip():
                        await item.click()
                        await page.wait_for_timeout(2_000)
                        print(f"  [date_range] {display_name}: set to '{option_text}'")
                        return True
                except Exception:
                    pass
        except Exception:
            pass

        # role=option fallback
        try:
            opts = page.get_by_role("option").filter(has_text=option_text)
            if await opts.count() > 0:
                await opts.first.click()
                await page.wait_for_timeout(2_000)
                print(f"  [date_range] {display_name}: set to '{option_text}'")
                return True
        except Exception:
            pass

    print(f"  [date_range] {display_name}: '어제' option not found")
    return False


def _parse_korean_count(text: str) -> Optional[int]:
    """Parse Korean ad count formats:
    약 3천개 → 3000 | 약 8천개 → 8000 | ~4개 → 4 | 1,234개 → 1234
    """
    m = re.search(r'약\s*(\d+(?:\.\d+)?)\s*(천|만|억)\s*개', text)
    if m:
        num = float(m.group(1))
        unit = {"천": 1_000, "만": 10_000, "억": 100_000_000}[m.group(2)]
        return int(num * unit)
    m = re.search(r'[~약]\s*(\d[\d,]*)\s*개', text)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r'(\d[\d,]*)\s*개(?:\s*의\s*광고)?', text)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


async def _extract_displayed_count(page: Page) -> tuple:
    """Extract the result count shown in the advertiser gallery page (not card aria-labels).

    Tries targeted CSS selectors first, then page text patterns.
    Returns (count: int | None, raw_text: str, selector_used: str).
    """
    for sel in [
        "div.ads-count",
        ".ads-count-searchable",
        "[class*='ads-count']",
        ".result-count",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                count = _parse_korean_count(text)
                if count is not None:
                    return count, text, sel
        except Exception:
            pass

    try:
        body = await page.evaluate("() => (document.body.innerText || '').slice(0, 8000)")
    except Exception:
        return None, "", ""

    for pattern in [
        r'광고\s+약\s*\d+(?:\.\d+)?\s*[천만억]\s*개',
        r'광고\s+약\s*\d[\d,]*\s*개',
        r'결과\s*~\s*\d[\d,]*\s*개',
        r'약\s*\d+(?:\.\d+)?\s*[천만억]\s*개',
        r'~\s*\d[\d,]*\s*개',
    ]:
        m = re.search(pattern, body)
        if m:
            snippet = m.group(0)
            count = _parse_korean_count(snippet)
            if count is not None:
                return count, snippet, "page_text"

    return None, "", ""


async def _extract_platform_counts(page: Page, gallery_url: str, display_name: str) -> dict:
    """Navigate to each platform URL and extract ad counts from aria-labels."""
    counts = {
        "total": None,
        "google_search": None,
        "youtube": None,
        "google_maps": None,
        "google_play": None,
        "google_shopping": None,
    }

    # Total count from base gallery URL (already on this page)
    total = await _extract_count_from_gallery(page)
    counts["total"] = total
    if total is None:
        print(f"  [count_not_found] {display_name}: no total count")
    else:
        print(f"  [GOOGLE_TC] {display_name}: total={total}")

    # Per-platform counts via URL navigation
    for platform_key, platform_param in _PLATFORMS.items():
        platform_url = f"{gallery_url}&platform={platform_param}"
        try:
            await page.goto(platform_url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
            await page.wait_for_timeout(2_000)
            count = await _extract_count_from_gallery(page)
            counts[platform_key] = count
            if count is None:
                print(f"  [count_not_found] {display_name}/{platform_key}: no count")
            else:
                print(f"  [GOOGLE_TC] {display_name}: {platform_key}={count}")
        except Exception as exc:
            print(f"  [platform_filter_not_found] {display_name}/{platform_key}: {exc}", file=sys.stderr)

    return counts


async def collect(
    competitor_key: str,
    display_name: str,
    advertiser_name: str,
    search_terms: Optional[list] = None,
    preferred_advertiser_name: Optional[str] = None,
    save_snapshot: bool = True,
) -> dict:
    """Return structured result:
    {
        total_count:               int | None,
        google_search_count:       int | None,   # always None (platform counts deferred)
        youtube_count:             int | None,
        google_maps_count:         int | None,
        google_play_count:         int | None,
        google_shopping_count:     int | None,
        source_url:                str,
        google_date_range:         str,          # "어제" on success, "" on failure
        status: "ok"|"date_filter_failed"|"advertiser_not_found"|"no_ads_today"|"count_not_found"|"failed",
        error:                     str | None,
        selected_advertiser_name:  str | None,
        search_term_used:          str | None,
    }
    """
    base = {
        "total_count": None,
        "google_search_count": None,
        "youtube_count": None,
        "google_maps_count": None,
        "google_play_count": None,
        "google_shopping_count": None,
        "source_url": _BASE_URL,
        "google_date_range": "",
        "status": "failed",
        "error": None,
        "selected_advertiser_name": None,
        "search_term_used": None,
    }

    terms = search_terms if search_terms else [advertiser_name]
    preferred = preferred_advertiser_name or advertiser_name

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        try:
            all_candidates: list[str] = []
            on_gallery = False
            current_url = _BASE_URL
            selected_name = ""
            search_term_used = ""

            for term in terms:
                print(f"  [GOOGLE_TC] {display_name}: trying search term '{term}'…")
                await page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)

                if not await _type_and_search(page, term):
                    snap = await _save_snapshot(page, competitor_key, "search_fail", save_snapshot)
                    base.update({"status": "failed", "error": f"search_input_not_found (snap: {snap})"})
                    print(f"  [search_input_not_found] {display_name}")
                    return base

                if not await _wait_for_suggestions(page):
                    msg = f"[{term}]: no suggestions"
                    all_candidates.append(msg)
                    print(f"  [advertiser_not_found] {display_name}: {msg}")
                    continue

                candidates = await _get_suggestion_texts(page)
                all_candidates.append(f"[{term}]: {candidates}")
                print(f"  [GOOGLE_TC] {display_name}: suggestions for '{term}' → {candidates}")

                selected_name = await _click_best_suggestion(page, preferred)
                await page.wait_for_timeout(1_500)

                on_gallery, current_url = await _wait_for_gallery(page)
                if not on_gallery:
                    clicked = await _handle_disambig(page)
                    if clicked:
                        await page.wait_for_timeout(1_500)
                        on_gallery, current_url = await _wait_for_gallery(page)

                if on_gallery:
                    search_term_used = term
                    break

                msg = f"[{term}]: gallery_not_reached url={current_url}"
                all_candidates.append(msg)
                print(f"  [gallery_not_reached] {display_name}: {msg}")

            if not on_gallery:
                snap = await _save_snapshot(page, competitor_key, "no_gallery", save_snapshot)
                error_msg = (
                    f"advertiser_not_found | tried: {terms} | "
                    f"candidates: {all_candidates} | snap: {snap}"
                )
                base.update({
                    "status": "advertiser_not_found",
                    "error": error_msg,
                    "selected_advertiser_name": None,
                    "search_term_used": None,
                })
                print(f"  [advertiser_not_found] {display_name}: all terms exhausted")
                return base

            print(f"  [GALLERY] {display_name}: {current_url} (selected: '{selected_name}')")
            await _save_snapshot(page, competitor_key, "gallery", save_snapshot)

            # Extract count from the visible result count area (default date range)
            total, raw_count_text, count_selector_used = await _extract_displayed_count(page)
            print(
                f"  [GOOGLE_TC] {display_name}: "
                f"raw_displayed_count_text='{raw_count_text}' "
                f"count_selector_used='{count_selector_used}' "
                f"parsed_google_total_ads_count={total}"
            )

            # Determine status
            if total is None:
                if await _check_no_ads(page):
                    status = "no_ads"
                    print(f"  [no_ads] {display_name}: zero ads on page")
                else:
                    status = "count_not_found"
                    print(f"  [count_not_found] {display_name}: could not extract count")
            else:
                status = "ok"
                print(f"  [GOOGLE_TC] {display_name}: total={total}")

            diag = f"selected: {selected_name} (search: {search_term_used})" if selected_name else ""
            if raw_count_text and status == "ok":
                diag = f"{diag} | count_text: {raw_count_text}" if diag else f"count_text: {raw_count_text}"

            base.update({
                "total_count": total,
                "google_search_count": None,
                "youtube_count": None,
                "google_maps_count": None,
                "google_play_count": None,
                "google_shopping_count": None,
                "source_url": current_url,
                "google_date_range": "",
                "status": status,
                "error": diag if diag else None,
                "selected_advertiser_name": selected_name or None,
                "search_term_used": search_term_used or None,
            })
            return base

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"  [FAILED] {display_name}: {msg}", file=sys.stderr)
            base.update({"status": "failed", "error": msg})
            return base
        finally:
            await browser.close()


def collect_sync(
    competitor_key: str,
    display_name: str,
    advertiser_name: str,
    search_terms: Optional[list] = None,
    preferred_advertiser_name: Optional[str] = None,
    save_snapshot: bool = True,
) -> dict:
    return asyncio.run(
        collect(
            competitor_key,
            display_name,
            advertiser_name,
            search_terms=search_terms,
            preferred_advertiser_name=preferred_advertiser_name,
            save_snapshot=save_snapshot,
        )
    )
