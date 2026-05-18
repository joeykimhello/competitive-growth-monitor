"""Meta Ad Library collector — fixed advertiser page URL.

Navigates directly to the competitor's pre-built page URL (search_type=page,
view_all_page_id=...). Never uses keyword search.

Per run:
  active_ad_count  — total active ads from the result counter
  visible_ad_count — number of cards successfully extracted
  creatives        — one dict per card:
      ad_id, ad_detail_url, ad_start_date, started_running_text,
      platforms, creative_text, landing_url, creative_type, creative_hash

Clicks '광고 상세 정보 보기' only when ad_id is not visible in the static card DOM.
"""

import asyncio
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from playwright.async_api import Page, async_playwright

_SNAPSHOT_DIR = Path(__file__).parents[3] / "data" / "snapshots" / "ads"
_PAGE_LOAD_TIMEOUT = 30_000
_SCROLL_PAUSE_MS = 1_500
_MAX_SCROLLS = 3

_UI_FRAGMENTS = frozenset({
    "log in", "sign up", "learn more", "see more", "로그인", "회원가입",
    "더 보기", "privacy policy", "terms of service", "cookie",
})


def _snapshot_path(competitor: str) -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _SNAPSHOT_DIR / f"{competitor}__meta__{ts}.html"


def _clean_url(raw: str) -> str:
    if not raw:
        return ""
    match = re.search(r"[?&]u=([^&]+)", raw)
    if match:
        return unquote(match.group(1))
    return raw.split("?")[0] if "facebook.com/l/" in raw else raw


def _is_ui_text(text: str) -> bool:
    if len(text) < 20:
        return True
    lower = text.lower()
    return any(frag in lower for frag in _UI_FRAGMENTS)


def _creative_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _parse_start_date(card_text: str) -> tuple[str, str]:
    """Parse Korean '2026. 5. 7.에 게재 시작함' → (raw_text, 'YYYY-MM-DD').
    Returns ('', '') when no match.
    """
    m = re.search(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*에\s*게재\s*시작함', card_text)
    if m:
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        return m.group(0).strip(), f"{y}-{mo}-{d}"
    m2 = re.search(r'[Ss]tarted\s+running\s+on\s+.{5,40}', card_text)
    if m2:
        return m2.group(0).strip(), ""
    return "", ""


def _extract_library_id(card_text: str) -> str:
    """Extract Meta Ad Library ID from visible card text.
    Example: '라이브러리 ID: 829771526364673'
    """
    m = re.search(r'라이브러리\s*ID\s*[:\s]\s*(\d{10,})', card_text)
    if m:
        return m.group(1)
    m = re.search(r'[Ll]ibrary\s+ID\s*[:\s]\s*(\d{10,})', card_text)
    if m:
        return m.group(1)
    return ""


async def _scroll_to_load(page: Page) -> None:
    for _ in range(_MAX_SCROLLS):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        await page.wait_for_timeout(_SCROLL_PAUSE_MS)


async def _extract_active_count(page: Page) -> tuple[Optional[int], str]:
    """Read total active ad count from the result counter text on the page.

    Returns (count, raw_matched_text). raw_matched_text is the verbatim page
    snippet used to derive the count (useful for debug logging).
    """
    result = await page.evaluate(r"""
        () => {
            const bodyText = document.body.innerText || '';

            // Korean: "결과 ~80개" / "결과 80개" (Meta Ad Library result header)
            let m = bodyText.match(/결과\s*~?\s*([\d,]+)\s*개/);
            if (m) return {num: m[1].replace(/,/g, ''), raw: m[0]};

            // Korean: "287개의 광고" or "총 287개" or "약 287개"
            m = bodyText.match(/(?:총\s*|약\s*)?([\d,]+)\s*개(?:의\s*광고)?/);
            if (m) return {num: m[1].replace(/,/g, ''), raw: m[0]};

            // English: "X of N ads/results"
            m = bodyText.match(/\bof\s+([\d,]+)\s*(ads?|results?)/i);
            if (m) return {num: m[1].replace(/,/g, ''), raw: m[0]};

            // English: standalone "N ads" in first 40 lines
            const lines = bodyText.split('\n').slice(0, 40);
            for (const line of lines) {
                const m2 = line.trim().match(/^([\d,]+)\s*(ads?|광고)$/i);
                if (m2) return {num: m2[1].replace(/,/g, ''), raw: m2[0]};
            }
            return null;
        }
    """)
    if result and isinstance(result, dict):
        num_str = str(result.get("num", ""))
        raw_text = str(result.get("raw", ""))
        if num_str:
            try:
                return int(num_str), raw_text
            except (ValueError, TypeError):
                pass
    return None, ""


async def _get_ad_id_from_card(card) -> str:
    """Extract ad ID from card element without any clicks."""
    for attr in ("data-ad-id", "id"):
        val = (await card.get_attribute(attr)) or ""
        if attr == "data-ad-id" and val:
            return val
        if attr == "id" and val.startswith("ad_id_"):
            return val[6:]

    el = await card.query_selector("[data-ad-id]")
    if el:
        return (await el.get_attribute("data-ad-id")) or ""

    for link in await card.query_selector_all("a[href*='?id='], a[href*='&id=']"):
        href = (await link.get_attribute("href")) or ""
        m = re.search(r'[?&]id=(\d+)', href)
        if m:
            return m.group(1)

    # Walk up ancestor chain for id^="ad_id_" (card may be a child of the ad container)
    try:
        js_id = await card.evaluate("""el => {
            let node = el;
            while (node) {
                if (node.id && node.id.startsWith('ad_id_')) return node.id.slice(6);
                node = node.parentElement;
            }
            return '';
        }""")
        if js_id:
            return str(js_id)
    except Exception:
        pass

    return ""


async def _try_click_detail_button(card, page: Page) -> None:
    """Click '광고 상세 정보 보기' if present. Silent on failure."""
    try:
        for btn in await card.query_selector_all("div[role='button'], button"):
            label = (await btn.inner_text()).strip()
            if "상세 정보 보기" in label or "광고 상세" in label:
                await btn.click()
                await page.wait_for_timeout(1_000)
                return
    except Exception:
        pass


async def _extract_creatives(page: Page, max_ads: int) -> list[dict]:
    """Extract ad data from full page innerText using regex.

    Card-element iteration is unreliable when the DOM structure changes.
    Instead, reads document.body.innerText and scans for pairs of:

        라이브러리 ID: <id>
        YYYY. M. D.에 게재 시작함

    The lazy [\s\S]{0,300}? between the two tokens ensures we grab the
    nearest date string after each library ID.
    """
    page_text = (await page.evaluate("document.body.innerText") or "").strip()

    _PAIR_RE = re.compile(
        r"라이브러리\s*ID[:：]?\s*(\d+)"
        r"[\s\S]{0,300}?"
        r"((\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*에\s*게재\s*시작함)",
        re.MULTILINE,
    )

    creatives: list[dict] = []
    seen_ids: set[str] = set()

    for match_idx, m in enumerate(_PAIR_RE.finditer(page_text)):
        if len(creatives) >= max_ads:
            break

        library_id = m.group(1)
        if library_id in seen_ids:
            continue
        seen_ids.add(library_id)

        started_text = m.group(2)
        y, mo, d = m.group(3), m.group(4).zfill(2), m.group(5).zfill(2)
        ad_start_date = f"{y}-{mo}-{d}"

        if match_idx < 3:
            ctx_start = max(0, m.start() - 100)
            ctx_end = min(len(page_text), m.end() + 200)
            ctx = page_text[ctx_start:ctx_end]
            print(f"  [META] Card {match_idx} context: {ctx[:500]!r}")
            print(
                f"  [META] Card {match_idx}: library_id={library_id!r} "
                f"started_running_text={started_text!r} ad_start_date={ad_start_date!r}"
            )

        creatives.append({
            "advertiser_name": "",
            "library_id": library_id,
            "ad_detail_url": f"https://www.facebook.com/ads/library/?id={library_id}",
            "ad_start_date": ad_start_date,
            "started_running_text": started_text,
            "platforms": "",
            "creative_text": "",
            "landing_url": "",
            "creative_type": "unknown",
            "creative_hash": "",
        })

    return creatives


async def collect(
    competitor_key: str,
    display_name: str,
    meta_ad_library_url: str,
    max_ads: int = 30,
    save_snapshot: bool = True,
) -> dict:
    """Return structured result:
    {
        active_ad_count:  int | None,   # from page result counter
        visible_ad_count: int,          # cards successfully extracted
        creatives:        list[dict],
        source_url:       str,
        status:           "ok" | "partial" | "failed",
        error:            str | None,
    }
    """
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
            print(f"  [META] Navigating for {display_name}…")
            await page.goto(
                meta_ad_library_url,
                wait_until="domcontentloaded",
                timeout=_PAGE_LOAD_TIMEOUT,
            )

            loaded = False
            for sel in [
                "._7jyr",
                "[data-testid='ad-library-ad-card']",
                "div[id^='ad_id_']",
                "[data-ad-id]",
                ".x1yztbdb",
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=10_000)
                    loaded = True
                    break
                except Exception:
                    continue

            if not loaded:
                print(
                    f"  [META] No ad card selector matched for {display_name}",
                    file=sys.stderr,
                )

            await _scroll_to_load(page)

            if save_snapshot:
                snap = _snapshot_path(competitor_key)
                snap.write_text(await page.content(), encoding="utf-8")
                print(f"  [META] Snapshot: {snap.name}")

            active_count, active_count_raw = await _extract_active_count(page)
            creatives = await _extract_creatives(page, max_ads)

            visible_count = len(creatives)
            if active_count is None and visible_count > 0:
                active_count = visible_count

            status = "ok" if active_count is not None else "partial"
            print(
                f"  [META] {display_name}: count={active_count}"
                f" raw={active_count_raw!r}, visible={visible_count}"
            )

            return {
                "active_ad_count": active_count,
                "active_ad_count_raw": active_count_raw,
                "visible_ad_count": visible_count,
                "creatives": creatives,
                "source_url": page.url,
                "status": status,
                "error": None,
            }

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"  [META] Error for {display_name}: {msg}", file=sys.stderr)
            return {
                "active_ad_count": None,
                "visible_ad_count": 0,
                "creatives": [],
                "source_url": meta_ad_library_url,
                "status": "failed",
                "error": msg,
            }
        finally:
            await browser.close()


def collect_sync(
    competitor_key: str,
    display_name: str,
    meta_ad_library_url: str,
    max_ads: int = 30,
    save_snapshot: bool = True,
) -> dict:
    return asyncio.run(
        collect(competitor_key, display_name, meta_ad_library_url, max_ads, save_snapshot)
    )
