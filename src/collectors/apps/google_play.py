"""Google Play Store collector — Android app version via Playwright.

Parses the public Play Store page (no API key required).
Uses Korean locale (hl=ko&gl=KR) so labels are in Korean.

Extraction strategy:
  1. Progressive scroll to load lazy-rendered sections.
  2. "새로운 기능" section → release_notes (expand "더보기" if present).
  3. Click "앱 정보 자세히 알아보기" button → app info popup.
  4. From popup text: "버전" label → version, "업데이트 날짜" → release_date (ISO).

Status:
  ok:        version extracted
  partial:   version missing but release_notes or release_date present
  not_found: 404 or "찾을 수 없" page text
  failed:    all fields missing or exception

Usage:
    result = collect_sync(package_id="com.samsamm2.mobileapp")

Returns:
    {
        app_name:     str,
        version:      str,
        release_date: str,   # ISO 8601, e.g. "2026-05-04"
        release_notes: str,
        source_url:   str,
        status:       "ok" | "partial" | "not_found" | "failed",
        error:        str | None,
    }
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import async_playwright, Page

_SNAPSHOT_DIR = Path(__file__).parents[3] / "data" / "snapshots" / "apps"
_PAGE_LOAD_TIMEOUT = 30_000
_RENDER_WAIT_MS = 3_000


def _snapshot_paths(package_id: str, suffix: str = "") -> Tuple[Path, Path]:
    """Return (html_path, png_path) for a snapshot pair."""
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = package_id.replace(".", "_")
    stem = f"{safe}__play__{ts}{suffix}"
    return _SNAPSHOT_DIR / f"{stem}.html", _SNAPSHOT_DIR / f"{stem}.png"


def _parse_ko_date(text: str) -> str:
    """Convert '2026. 5. 4.' or '2026년 5월 4일' to '2026-05-04'."""
    m = re.search(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return text.strip()


async def _extract_app_name(page: Page) -> str:
    try:
        el = await page.query_selector('h1[itemprop="name"]')
        if el:
            return (await el.inner_text()).strip()
        title = await page.title()
        if " - " in title:
            return title.split(" - ")[0].strip()
        return title.strip()
    except Exception:
        return ""


async def _extract_release_notes(page: Page) -> Tuple[str, bool]:
    """Return (release_notes, section_found).

    Tries to expand "더보기" in the "새로운 기능" section before extracting.
    """
    page_text = (await page.evaluate("document.body.innerText") or "")
    if "새로운 기능" not in page_text:
        return "", False

    # Expand truncated notes if a "더보기" button is visible
    for btn_selector in [
        'button:has-text("더보기")',
        'button:has-text("더 보기")',
        '[role="button"]:has-text("더보기")',
        '[role="button"]:has-text("더 보기")',
    ]:
        try:
            btn = page.locator(btn_selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(600)
                page_text = (await page.evaluate("document.body.innerText") or "")
                break
        except Exception:
            pass

    # Extract content between "새로운 기능" and the next major section / UI element
    m = re.search(
        r'새로운\s*기능\s*\n([\s\S]{1,1000}?)'
        r'(?='
        r'\n부적절한\s*앱으로\s*신고'
        r'|\n앱\s*지원'
        r'|\n유사한\s*앱'
        r'|\n앱\s*정보\b'
        r'|\n평가\s*및\s*리뷰'
        r'|\n개발자\s*연락처'
        r'|\Z)',
        page_text,
    )
    if m:
        notes = m.group(1).strip()
        # Strip residual button text and Material icon names (e.g. "flag", "expand_more")
        notes = re.sub(r'\n?더\s*보기\s*$', '', notes).strip()
        notes = re.sub(r'\nflag\s*$', '', notes).strip()
        return notes, True

    return "", True  # section found but extraction failed


async def _open_app_info_panel(page: Page) -> bool:
    """Click '앱 정보 자세히 알아보기' to open the app info popup.

    Tries four strategies in order; returns True on success.
    """
    # Strategy 1: aria-label on button or any element
    for selector in [
        'button[aria-label="앱 정보 자세히 알아보기"]',
        '[aria-label="앱 정보 자세히 알아보기"]',
        'button[aria-label*="앱 정보"]',
        '[aria-label*="앱 정보 자세히"]',
    ]:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                await el.scroll_into_view_if_needed()
                await el.click()
                await page.wait_for_timeout(1500)
                print(f"  [PLAY] 앱 정보 버튼 클릭 성공 (selector={selector!r})")
                return True
        except Exception:
            pass

    # Strategy 2: "앱 정보" heading → walk to nearby button
    for heading_sel in [
        'h2:has-text("앱 정보")',
        '[role="heading"]:has-text("앱 정보")',
    ]:
        try:
            heading = page.locator(heading_sel).first
            if await heading.count() == 0:
                continue
            for xpath in ["xpath=../button", "xpath=../../button", "xpath=../../.."]:
                try:
                    btn = heading.locator(xpath).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.scroll_into_view_if_needed()
                        await btn.click()
                        await page.wait_for_timeout(1500)
                        print(f"  [PLAY] 앱 정보 버튼 클릭 성공 (heading+xpath={xpath!r})")
                        return True
                except Exception:
                    pass
        except Exception:
            pass

    print("  [PLAY] app_info_button_not_found", file=sys.stderr)
    return False


async def _extract_from_popup(page: Page) -> Tuple[str, str, str]:
    """Return (version, release_date, popup_text) from the app info popup."""
    popup_text = ""

    # Try dialog/modal selectors first so we don't scan the whole page
    for selector in [
        '[role="dialog"]',
        'div[aria-modal="true"]',
    ]:
        try:
            el = page.locator(selector).last
            if await el.count() > 0 and await el.is_visible():
                await el.evaluate("el => el.scrollTo(0, el.scrollHeight)")
                await page.wait_for_timeout(700)
                popup_text = (await el.inner_text()).strip()
                if len(popup_text) > 50:
                    break
        except Exception:
            pass

    if not popup_text:
        # Fallback: full page innerText (popup may be rendered inline)
        popup_text = (await page.evaluate("document.body.innerText") or "")

    # Extract version: "버전\n3.4.30" or "버전 3.4.30"
    version = ""
    m = re.search(r'(?:^|\n)버전\s*\n?\s*([\d]+(?:\.[\d]+)+)', popup_text)
    if m:
        version = m.group(1).strip()

    # Extract release_date: "업데이트 날짜\n2026. 5. 4."
    release_date = ""
    m = re.search(r'업데이트\s*날짜\s*\n?\s*(\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?)', popup_text)
    if m:
        release_date = _parse_ko_date(m.group(1))
    else:
        m = re.search(r'업데이트\s*날짜\s*\n?\s*(\d{4}년\s*\d{1,2}월\s*\d{1,2}일)', popup_text)
        if m:
            release_date = _parse_ko_date(m.group(1))

    # Debug-only: log extra fields (not written to sheet)
    for label in ["필요한 Android 버전", "다운로드", "개발자"]:
        m2 = re.search(rf'{label}\s*\n?\s*([^\n]+)', popup_text)
        if m2:
            print(f"  [PLAY] [debug] {label}: {m2.group(1).strip()!r}")

    return version, release_date, popup_text


async def collect(package_id: str, save_snapshot: bool = True) -> dict:
    """Return version info for an Android app from the public Play Store page."""
    source_url = (
        f"https://play.google.com/store/apps/details?id={package_id}&hl=ko&gl=KR"
    )
    base: dict = {
        "app_name": "",
        "version": "",
        "release_date": "",
        "release_notes": "",
        "source_url": source_url,
        "status": "failed",
        "error": None,
    }

    print(f"  [PLAY] normalized_url: {source_url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
            },
        )
        page = await context.new_page()

        try:
            resp = await page.goto(
                source_url,
                wait_until="domcontentloaded",
                timeout=_PAGE_LOAD_TIMEOUT,
            )

            if resp and resp.status == 404:
                print(f"  [PLAY] Not found (404): {package_id}")
                base["status"] = "not_found"
                return base

            # Wait for initial JS render
            await page.wait_for_timeout(_RENDER_WAIT_MS)

            # Adaptive scroll: keep going until "새로운 기능" appears or page height
            # stops growing (stagnant ≥ 3 consecutive rounds).
            # mouse.wheel nudge triggers IntersectionObserver in addition to scrollTo.
            prev_height = 0
            stagnant = 0
            for attempt in range(12):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(800)
                await page.mouse.wheel(0, 1000)
                await page.wait_for_timeout(300)
                new_height = await page.evaluate("document.body.scrollHeight")
                _text_check = (await page.evaluate("document.body.innerText") or "")
                if "새로운 기능" in _text_check:
                    print(f"  [PLAY] 새로운 기능 섹션 감지 (scroll attempt {attempt + 1})")
                    break
                if new_height == prev_height:
                    stagnant += 1
                    if stagnant >= 3:
                        break
                else:
                    stagnant = 0
                prev_height = new_height

            app_name = await _extract_app_name(page)

            # ── Step 1: release_notes from "새로운 기능" ──────────────────────
            release_notes, notes_found = await _extract_release_notes(page)
            print(f"  [PLAY] 새로운 기능 섹션 발견: {notes_found}")
            print(f"  [PLAY] raw_release_notes: {release_notes[:300]!r}")

            # Main page snapshot (before popup)
            if save_snapshot:
                html_path, png_path = _snapshot_paths(package_id)
                html_path.write_text(await page.content(), encoding="utf-8")
                await page.screenshot(path=str(png_path), full_page=False)
                print(f"  [PLAY] snapshot: {html_path.name}")

            # ── Step 2: open "앱 정보" popup ──────────────────────────────────
            popup_opened = await _open_app_info_panel(page)
            print(f"  [PLAY] 앱 정보 버튼 클릭 성공: {popup_opened}")

            version = ""
            release_date = ""

            if popup_opened:
                version, release_date, popup_text = await _extract_from_popup(page)
                print(f"  [PLAY] 팝업 visible text 앞 1000자: {popup_text[:1000]!r}")

                if save_snapshot:
                    html2, png2 = _snapshot_paths(package_id, suffix="__popup")
                    html2.write_text(await page.content(), encoding="utf-8")
                    await page.screenshot(path=str(png2), full_page=False)
                    print(f"  [PLAY] popup snapshot: {html2.name}")
            else:
                print("  [PLAY] 팝업 visible text 앞 1000자: (팝업 열기 실패)")

            print(f"  [PLAY] parsed version: {version!r}")
            print(f"  [PLAY] parsed release_date: {release_date!r}")

            # ── Status determination ───────────────────────────────────────────
            if version or release_date:
                status = "ok"
            elif release_notes:
                status = "partial"
            else:
                page_text = (await page.evaluate("document.body.innerText") or "")
                if "찾을 수 없" in page_text or "not found" in page_text.lower():
                    print(f"  [PLAY] Not found (text): {package_id}")
                    base["status"] = "not_found"
                    return base
                status = "failed"
                base["error"] = "version_notes_date_all_missing"

            print(f"  [PLAY] final status: {status}")

            return {
                "app_name": app_name,
                "version": version,
                "release_date": release_date,
                "release_notes": release_notes,
                "source_url": source_url,
                "status": status,
                "error": None if status in ("ok", "partial") else base.get("error"),
            }

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"  [PLAY] Error for {package_id}: {msg}", file=sys.stderr)
            base["error"] = msg
            return base
        finally:
            await browser.close()


def collect_sync(package_id: str, save_snapshot: bool = True) -> dict:
    return asyncio.run(collect(package_id, save_snapshot))
