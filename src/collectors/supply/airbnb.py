"""Airbnb supply collector — sourced from AirDNA Total Active Listings.

AirDNA requires an authenticated user session. Run scripts/setup_airdna_session.py
once to save the session to .auth/airdna_state.json. Without that file this
collector always returns status=login_required.

Extraction strategy:
1. Navigate to the configured AirDNA listings URL.
2. Detect login redirect or login-wall text → return status=login_required.
3. Retry loop (up to 20 s): wait for 'Total Active Listings' text or substantial
   page content to appear.
4. Strategy A: regex on page text — inline label+number pattern.
5. Strategy B: CSS selectors for metric card elements.
6. Strategy C: locate 'Total Active Listings' label element; try sibling node,
   then parent container, then card-ancestor innerText.
7. Strategy D: broader 'Active Listings' / 'Listings' label proximity patterns.
8. Strategy E: 'Total Active Listings' present in text but inline parse failed —
   extract nearby number candidates.

Returns dict: {count, raw_count_text, status, error}.

Snapshots saved to: data/snapshots/supply/airbnb__{region}__{timestamp}.html
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

_SNAPSHOT_DIR = Path(__file__).parents[3] / "data" / "snapshots" / "supply"
_AUTH_STATE_FILE = Path(__file__).parents[3] / ".auth" / "airdna_state.json"
_PAGE_LOAD_TIMEOUT = 30_000
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_LOGIN_URL_PATTERNS = ["/login", "/signin", "/auth", "/subscribe", "/upgrade"]

_METRIC_LABEL_SELECTORS = [
    "[class*='metric'][class*='active']",
    "[class*='stat'][class*='active']",
    "[class*='kpi'][class*='active']",
    "[data-metric='total_active_listings']",
    "[data-testid*='active-listing']",
    "[data-testid*='total-active']",
]

_INLINE_PATTERNS = [
    r"Total\s+Active\s+Listings?\s*[:\n]?\s*([0-9][0-9,]*)",
    r"Total\s+Active\s+Rentals?\s*[:\n]?\s*([0-9][0-9,]*)",
    r"Active\s+Listings?\s*[:\n]?\s*([0-9][0-9,]*)",
]

# Broader fallback: label followed within 80 chars by a 3+-digit number
_BROAD_PATTERNS = [
    (r"Active\s+Listings?", r"([0-9][0-9,]{2,})"),
    (r"\bListings?\b",      r"([0-9][0-9,]{2,})"),
]


def _snapshot_path(region_key: str) -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _SNAPSHOT_DIR / f"airbnb__{region_key}__{ts}.html"


def _parse_count(text: str) -> Optional[int]:
    m = re.search(r"([0-9][0-9,]{2,})", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _is_login_redirect(url: str) -> bool:
    return any(p in url.lower() for p in _LOGIN_URL_PATTERNS)


async def _collect(
    region_key: str,
    region_label: str,
    url: str,
    save_snapshot: bool,
) -> dict:
    if not _AUTH_STATE_FILE.exists():
        print(
            f"  [LOGIN_REQUIRED] airbnb/airdna / {region_label}: "
            f"{_AUTH_STATE_FILE} not found — run scripts/setup_airdna_session.py",
            file=sys.stderr,
        )
        return {"count": None, "raw_count_text": "", "status": "login_required", "error": "airdna_state_file_missing"}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(_AUTH_STATE_FILE),
            user_agent=_USER_AGENT,
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        try:
            print(f"  [CHECKED] airbnb/airdna / {region_label} — {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)

            # ── Login redirect check ─────────────────────────────────────────
            current_url = page.url
            if _is_login_redirect(current_url):
                print(
                    f"  [LOGIN_REQUIRED] airbnb/airdna / {region_label}: redirected to {current_url}",
                    file=sys.stderr,
                )
                if save_snapshot:
                    snap = _snapshot_path(region_key)
                    snap.write_text(await page.content(), encoding="utf-8")
                    print(f"  [SNAPSHOT] {snap.name} (login redirect)")
                return {"count": None, "raw_count_text": "", "status": "login_required", "error": "airdna_login_required"}

            # ── Retry loop: wait up to ~20 s for meaningful content ──────────
            # Break only when both the label AND the count number have rendered.
            # The label appears ~5 s before the number; breaking on label alone
            # causes all strategies to fail (no number in text yet).
            page_text = ""
            for attempt in range(4):
                await page.wait_for_timeout(5_000)
                page_text = (await page.evaluate("document.body.innerText") or "").strip()
                has_tal = "Total Active Listings" in page_text
                has_number_loaded = has_tal and bool(
                    re.search(r"Total\s+Active\s+Listings[\s\S]{0,80}\d{4,}", page_text)
                )
                print(
                    f"  [DEBUG] airdna attempt={attempt + 1} url={page.url} "
                    f"text_len={len(page_text)} has_total_active_listings={has_tal} "
                    f"has_number_loaded={has_number_loaded}"
                )
                if has_number_loaded or len(page_text) > 5000:
                    break

            # ── Login-wall check (after content loads) ───────────────────────
            login_keywords = ["log in", "sign in", "login", "signin", "subscribe", "upgrade your plan"]
            if any(kw in page_text.lower() for kw in login_keywords) and len(page_text) < 2000:
                print(
                    f"  [LOGIN_REQUIRED] airbnb/airdna / {region_label}: login wall in page text",
                    file=sys.stderr,
                )
                if save_snapshot:
                    snap = _snapshot_path(region_key)
                    snap.write_text(await page.content(), encoding="utf-8")
                    print(f"  [SNAPSHOT] {snap.name} (login wall)")
                return {"count": None, "raw_count_text": "", "status": "login_required", "error": "airdna_login_required"}

            if save_snapshot:
                snap = _snapshot_path(region_key)
                snap.write_text(await page.content(), encoding="utf-8")
                print(f"  [SNAPSHOT] {snap.name}")

            has_tal_text = "Total Active Listings" in page_text
            print(f"  [DEBUG] airdna has_total_active_listings_in_text={has_tal_text}")

            # ── Strategy A: inline label+number pattern in page text ─────────
            for pattern in _INLINE_PATTERNS:
                m = re.search(pattern, page_text, re.IGNORECASE)
                if m:
                    try:
                        count = int(m.group(1).replace(",", ""))
                        raw_text = m.group(0).strip()
                        print(f"  [DEBUG] airdna strategy=A raw='{raw_text}'")
                        print(f"  [COLLECTED] airbnb/airdna / {region_label}: {count} raw='{raw_text}'")
                        return {"count": count, "raw_count_text": raw_text, "status": "ok", "error": None}
                    except ValueError:
                        continue

            # ── Strategy B: CSS selector for metric card elements ────────────
            for sel in _METRIC_LABEL_SELECTORS:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    count = _parse_count(text)
                    if count is not None:
                        print(f"  [DEBUG] airdna strategy=B selector='{sel}' raw='{text[:100]}'")
                        print(f"  [COLLECTED] airbnb/airdna / {region_label}: {count} (CSS selector)")
                        return {"count": count, "raw_count_text": text[:200], "status": "ok", "error": None}

            # ── Strategy C: label element — sibling, parent, card container ──
            for label_text in ["Total Active Listings", "Total Active Rentals"]:
                label_el = await page.query_selector(f"text={label_text}")
                if not label_el:
                    continue

                # C1: next sibling element
                sibling_text = await page.evaluate(
                    "(el) => el.nextElementSibling?.innerText || ''", label_el
                )
                count = _parse_count(sibling_text)
                if count is not None:
                    raw = f"{label_text}: {sibling_text.strip()[:100]}"
                    print(f"  [DEBUG] airdna strategy=C1 (sibling) raw='{raw}'")
                    print(f"  [COLLECTED] airbnb/airdna / {region_label}: {count}")
                    return {"count": count, "raw_count_text": raw, "status": "ok", "error": None}

                # C2: parent element innerText
                parent_text = await page.evaluate(
                    "(el) => el.parentElement?.innerText || ''", label_el
                )
                count = _parse_count(parent_text)
                if count is not None:
                    raw = parent_text.strip()[:200]
                    print(f"  [DEBUG] airdna strategy=C2 (parent) raw='{raw}'")
                    print(f"  [COLLECTED] airbnb/airdna / {region_label}: {count}")
                    return {"count": count, "raw_count_text": raw, "status": "ok", "error": None}

                # C3: card/stat/metric ancestor
                card_text = await page.evaluate(
                    "(el) => el.closest('[class*=\"card\"],[class*=\"stat\"],[class*=\"metric\"],[class*=\"kpi\"]')?.innerText || ''",
                    label_el,
                )
                count = _parse_count(card_text)
                if count is not None:
                    raw = card_text.strip()[:200]
                    print(f"  [DEBUG] airdna strategy=C3 (ancestor) raw='{raw}'")
                    print(f"  [COLLECTED] airbnb/airdna / {region_label}: {count}")
                    return {"count": count, "raw_count_text": raw, "status": "ok", "error": None}

            # ── Strategy D: broader label proximity in page text ─────────────
            for label_pat, num_pat in _BROAD_PATTERNS:
                m_label = re.search(label_pat, page_text, re.IGNORECASE)
                if m_label:
                    window = page_text[m_label.start(): m_label.start() + 120]
                    m_num = re.search(num_pat, window)
                    if m_num:
                        try:
                            count = int(m_num.group(1).replace(",", ""))
                            raw = window.strip()[:120]
                            print(f"  [DEBUG] airdna strategy=D raw='{raw}'")
                            print(f"  [COLLECTED] airbnb/airdna / {region_label}: {count} (broad label)")
                            return {"count": count, "raw_count_text": raw, "status": "ok", "error": None}
                        except ValueError:
                            continue

            # ── Strategy E: TAL present in text, extract nearby candidates ───
            if has_tal_text:
                idx = page_text.lower().find("total active listings")
                window = page_text[idx: idx + 150]
                candidates = re.findall(r"\b([0-9][0-9,]{2,})\b", window)
                print(f"  [DEBUG] airdna strategy=E candidates={candidates}")
                if candidates:
                    try:
                        count = int(candidates[0].replace(",", ""))
                        raw = window.strip()[:150]
                        print(f"  [COLLECTED] airbnb/airdna / {region_label}: {count} (nearby candidate)")
                        return {"count": count, "raw_count_text": raw, "status": "ok", "error": None}
                    except ValueError:
                        pass

            print(
                f"  [NO_RESULTS] airbnb/airdna / {region_label}: "
                "logged in but Total Active Listings value not extracted after retry — check snapshot",
                file=sys.stderr,
            )
            return {
                "count": None,
                "raw_count_text": "",
                "status": "count_not_found",
                "error": "airdna_total_active_listings_not_found_after_retry",
            }

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"  [FAILED] airbnb/airdna / {region_label}: {msg}", file=sys.stderr)
            if save_snapshot:
                try:
                    snap = _snapshot_path(region_key)
                    snap.write_text(await page.content(), encoding="utf-8")
                    print(f"  [SNAPSHOT] {snap.name} (on error)")
                except Exception:
                    pass
            return {"count": None, "raw_count_text": "", "status": "failed", "error": msg}
        finally:
            await context.close()


def collect_sync(
    competitor_key: str,
    region_key: str,
    region_label: str,
    url: str,
    save_snapshot: bool = True,
) -> dict:
    """Return dict: {count, raw_count_text, status, error}.

    Requires .auth/airdna_state.json — run scripts/setup_airdna_session.py first.
    """
    return asyncio.run(_collect(region_key, region_label, url, save_snapshot))
