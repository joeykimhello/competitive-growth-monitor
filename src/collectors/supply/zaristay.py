"""Zaristay supply collector — listing count via zaritalk.com search results.

Requires a Zaritalk login session. Run scripts/setup_zaritalk_session.py once
to save the session to .auth/zaritalk_state.json. Without that file, or if
the session has expired, this collector always returns status=login_required.

Extraction strategy:
  Seoul   : single URL → parse '이 지역 방 N개 보기' button text.
  Nationwide: iterate 10 regional URLs in one browser session, parse each,
              then sum successful counts and return a single row.

The count pattern supported:
  이 지역 방 953개 보기
  이 지역 방 1,234개 보기
  방 953개 보기

Returns dict: {count, raw_count_text, status, error}.

Snapshots saved to: data/snapshots/supply/zaristay__{region}__{timestamp}.html
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

_SNAPSHOT_DIR = Path(__file__).parents[3] / "data" / "snapshots" / "supply"
_AUTH_STATE_FILE = Path(__file__).parents[3] / ".auth" / "zaritalk_state.json"
_LOGIN_URL_PATTERNS = ["/login", "/signin", "/auth"]
_PAGE_LOAD_TIMEOUT = 30_000
_COUNT_WAIT_TIMEOUT = 15_000
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Regional URLs for nationwide sum (순회 순서 유지)
_REGIONAL_URLS: list[tuple[str, str]] = [
    ("서울",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C"),
    ("강원",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EA%B0%95%EC%9B%90"),
    ("경기",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EA%B2%BD%EA%B8%B0%EB%8F%84"),
    ("충북",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EC%B6%A9%EC%B2%AD%EB%8F%84"),
    ("충남",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EC%B6%A9%EB%82%A8"),
    ("전북",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EC%A0%84%EB%9D%BC%EB%8F%84"),
    ("전남",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EC%A0%84%EB%82%A8"),
    ("경북",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EA%B2%BD%EC%83%81%EB%B6%81%EB%8F%84"),
    ("경남",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EA%B2%BD%EC%83%81%EB%82%A8%EB%8F%84"),
    ("제주",  "https://tenant.zaritalk.com/short-term-vacancy?query=%EC%A0%9C%EC%A3%BC%EB%8F%84"),
]

# Strict CTA pattern — only matches the bottom button text, not map markers or prices
_CTA_RE = re.compile(r"이\s*지역\s*방\s*([\d,]+)\s*개\s*보기")
_LOGIN_KEYWORDS = ["로그인", "로그인이 필요", "login", "sign in"]

# CTA button/anchor selectors tried in order before falling back to full page text
_CTA_SELECTORS = [
    "button",
    "a[class*='cta']",
    "a[class*='button']",
    "[class*='cta']",
    "[class*='bottom']",
    "[class*='fixed']",
    "[class*='footer']",
    "a",
]


def _is_login_redirect(url: str) -> bool:
    return any(p in url.lower() for p in _LOGIN_URL_PATTERNS)


def _snapshot_path(region_key: str) -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _SNAPSHOT_DIR / f"zaristay__{region_key}__{ts}.html"


async def _extract_cta_room_count(page) -> tuple[Optional[int], str]:
    """Extract count from the bottom CTA button '이 지역 방 N개 보기'.

    Tries visible button/anchor elements containing both '이 지역 방' and '보기'
    before falling back to a full page text scan. Never uses generic number
    extraction — only _CTA_RE matches are accepted.
    """
    for sel in _CTA_SELECTORS:
        try:
            elements = await page.query_selector_all(sel)
        except Exception:
            continue
        for el in elements:
            try:
                text = (await el.inner_text()).strip()
                if "이 지역 방" in text and "보기" in text:
                    m = _CTA_RE.search(text)
                    if m:
                        return int(m.group(1).replace(",", "")), text
            except Exception:
                continue

    # Fallback: full page innerText (still requires the strict CTA pattern)
    page_text = (await page.evaluate("document.body.innerText") or "").strip()
    m = _CTA_RE.search(page_text)
    if m:
        return int(m.group(1).replace(",", "")), m.group(0)

    return None, ""


async def _load_and_extract(page, url: str, region_label: str) -> tuple[Optional[int], str]:
    """Navigate to url, wait for CTA button text, extract and log count."""
    await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
    await page.wait_for_timeout(2_000)

    try:
        await page.wait_for_function(
            "() => { const t = document.body.innerText;"
            " return t.includes('이 지역 방') && t.includes('보기'); }",
            timeout=_COUNT_WAIT_TIMEOUT,
        )
    except PlaywrightTimeoutError:
        pass

    actual_url = page.url
    count, raw_cta = await _extract_cta_room_count(page)

    print(
        f"  [ZARISTAY] region={region_label}"
        f" actual_url={actual_url}"
        f" raw_cta={raw_cta!r}"
        f" parsed_count={count}"
    )
    return count, raw_cta


def _login_required_result() -> dict:
    return {
        "count": None,
        "raw_count_text": "",
        "status": "login_required",
        "error": "zaritalk_login_required",
    }


async def _check_login_wall(page) -> bool:
    """Return True if the current page looks like a login wall."""
    if _is_login_redirect(page.url):
        return True
    page_text = (await page.evaluate("document.body.innerText") or "").strip()
    if any(kw in page_text for kw in _LOGIN_KEYWORDS) and len(page_text) < 2000:
        return True
    return False


async def _collect(
    region_key: str,
    region_label: str,
    url: str,
    save_snapshot: bool,
) -> dict:
    if not _AUTH_STATE_FILE.exists():
        print(
            f"  [LOGIN_REQUIRED] zaristay / {region_label}: "
            f"{_AUTH_STATE_FILE} not found — run scripts/setup_zaritalk_session.py",
            file=sys.stderr,
        )
        return _login_required_result()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(_AUTH_STATE_FILE),
            user_agent=_USER_AGENT,
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        try:
            if region_key == "nationwide":
                # Quick login check on first regional URL before iterating all
                first_url = _REGIONAL_URLS[0][1]
                await page.goto(first_url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
                await page.wait_for_timeout(2_000)
                if await _check_login_wall(page):
                    print(
                        f"  [LOGIN_REQUIRED] zaristay / nationwide: session expired",
                        file=sys.stderr,
                    )
                    return _login_required_result()
                return await _collect_nationwide(page, save_snapshot)

            # ── Single-region (e.g. seoul) ────────────────────────────────
            print(f"  [CHECKED] zaristay / {region_label} — {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
            await page.wait_for_timeout(2_000)

            if await _check_login_wall(page):
                print(
                    f"  [LOGIN_REQUIRED] zaristay / {region_label}: "
                    f"redirected to {page.url}",
                    file=sys.stderr,
                )
                if save_snapshot:
                    snap = _snapshot_path(region_key)
                    snap.write_text(await page.content(), encoding="utf-8")
                    print(f"  [SNAPSHOT] {snap.name} (login wall)")
                return _login_required_result()

            try:
                await page.wait_for_function(
                    "() => { const t = document.body.innerText;"
                    " return t.includes('이 지역 방') && t.includes('보기'); }",
                    timeout=_COUNT_WAIT_TIMEOUT,
                )
            except PlaywrightTimeoutError:
                pass

            if save_snapshot:
                snap = _snapshot_path(region_key)
                snap.write_text(await page.content(), encoding="utf-8")
                print(f"  [SNAPSHOT] {snap.name}")

            count, raw_cta = await _extract_cta_room_count(page)
            print(
                f"  [ZARISTAY] region={region_label}"
                f" actual_url={page.url}"
                f" raw_cta={raw_cta!r}"
                f" parsed_count={count}"
            )
            if count is not None:
                return {"count": count, "raw_count_text": raw_cta, "status": "ok", "error": None}

            page_text_len = len((await page.evaluate("document.body.innerText") or ""))
            print(
                f"  [NO_RESULTS] zaristay / {region_label}: CTA not found "
                f"(text_len={page_text_len})",
                file=sys.stderr,
            )
            return {
                "count": None, "raw_count_text": "", "status": "count_not_found",
                "error": "zaristay_count_not_found",
            }

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"  [FAILED] zaristay / {region_label}: {msg}", file=sys.stderr)
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


async def _collect_nationwide(page, save_snapshot: bool) -> dict:
    """Iterate regional URLs, sum counts, return one result row."""
    regional_counts: dict[str, int] = {}
    failed_regions: list[str] = []

    for region_label, region_url in _REGIONAL_URLS:
        try:
            count, _ = await _load_and_extract(page, region_url, region_label)
            # If the session expired mid-run, abort and return login_required
            if await _check_login_wall(page):
                print(
                    f"  [LOGIN_REQUIRED] zaristay / nationwide: session expired at {region_label}",
                    file=sys.stderr,
                )
                return _login_required_result()
            if count is not None:
                regional_counts[region_label] = count
            else:
                failed_regions.append(region_label)
        except Exception as exc:
            print(
                f"  [ZARISTAY] {region_label}: exception {exc}",
                file=sys.stderr,
            )
            failed_regions.append(region_label)

        if save_snapshot:
            snap = _snapshot_path(f"nationwide_{region_label}")
            try:
                snap.write_text(await page.content(), encoding="utf-8")
            except Exception:
                pass

    if not regional_counts:
        return {
            "count": None, "raw_count_text": "", "status": "count_not_found",
            "error": "all_regions_failed",
        }

    total = sum(regional_counts.values())
    breakdown = "; ".join(f"{r}={c}" for r, c in regional_counts.items())
    status = "partial" if failed_regions else "ok"
    error = f"failed_regions: {', '.join(failed_regions)}" if failed_regions else None

    print(f"  [ZARISTAY] nationwide_sum={total:,}  breakdown={breakdown}")
    if failed_regions:
        print(f"  [ZARISTAY] failed_regions={failed_regions}", file=sys.stderr)

    return {
        "count": total,
        "raw_count_text": breakdown[:500],
        "status": status,
        "error": error,
    }


def collect_sync(
    competitor_key: str,
    region_key: str,
    region_label: str,
    url: str,
    save_snapshot: bool = True,
) -> dict:
    """Return dict: {count, raw_count_text, status, error}."""
    return asyncio.run(_collect(region_key, region_label, url, save_snapshot))
