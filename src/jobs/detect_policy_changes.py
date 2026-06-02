"""Detect new or changed posts on competitor notice board pages.

Usage:
    python -m src.jobs.detect_policy_changes

For each page in config/policy_pages.yaml:
  - Fetches the notice board URL (no login required)
  - Extracts the latest post (title, URL, published date)
  - Compares with stored state in data/snapshots/policy/
  - is_new:     latest URL changed (new post at the top)
  - is_changed: latest title changed while URL stayed the same
  - Writes one row to policy_updates per page checked
  - First run: saves initial state, writes row with is_new=False

Pages with js_rendered: true are fetched via Playwright.
LiveAnywhere uses role-based Playwright extraction (Next.js).
Other js_rendered pages (e.g. Encostay Zendesk) use Playwright HTML + BS4.
Detail page body (not listing page text) is stored as raw_text.

Status values in result dicts:
  ok                      successfully checked, no change or change detected
  fetch_failed            listing page could not be loaded
  no_post                 listing loaded but no post could be extracted
  detail_navigation_failed detail URL could not be fetched/rendered
  image_needs_review      post appears to be an image post
  failed                  unexpected error

Returns a stats dict consumed by run_daily.py.
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from src.integrations.google_sheets import append_row, ensure_headers

load_dotenv()

_CONFIG_PATH = Path(__file__).parents[2] / "config" / "policy_pages.yaml"
_SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots" / "policy"
_REQUEST_TIMEOUT = 20
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}


# ── Snapshot helpers ─────────────────────────────────────────────────────────

def _snapshot_path(competitor: str, key: str) -> Path:
    return _SNAPSHOT_DIR / f"{competitor}__{key}.json"


def _load_snapshot(competitor: str, key: str) -> Optional[dict]:
    path = _snapshot_path(competitor, key)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [warn] could not read snapshot {path.name}: {exc}", file=sys.stderr)
        return None


def _save_snapshot(competitor: str, key: str, data: dict) -> None:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with open(_snapshot_path(competitor, key), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Page fetch (requests) ─────────────────────────────────────────────────────

def _fetch_page(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.RequestException as exc:
        print(f"  [warn] fetch failed for {url}: {exc}", file=sys.stderr)
        return None


def _extract_body_text(html: str) -> str:
    """Extract article body text from HTML using BS4."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    for sel in [
        "article", ".article-body", "main", ".content", "#content",
        ".post-content", ".entry-content",
    ]:
        el = soup.select_one(sel)
        if el:
            return " ".join(el.get_text(separator=" ").split())[:1000]
    return " ".join(soup.get_text(separator=" ").split())[:1000]


def _fetch_detail_body(url: str) -> str:
    """Fetch article detail page via requests and return body text excerpt."""
    if not url:
        return ""
    html = _fetch_page(url)
    if not html:
        return ""
    return _extract_body_text(html)


# ── Playwright fetch helpers ──────────────────────────────────────────────────

async def _playwright_get_html(url: str) -> Optional[str]:
    """Fetch page HTML via Playwright (headless, networkidle). Returns None on failure."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_UA,
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            await page.wait_for_timeout(2_000)
            return await page.content()
        except Exception as exc:
            print(f"  [warn] Playwright fetch failed {url}: {exc}", file=sys.stderr)
            return None
        finally:
            await browser.close()


def _normalize_date(raw: str) -> str:
    """Normalize date strings like '2025.09.22', '2025-09-22', '2025/09/22' to 'YYYY-MM-DD'."""
    m = re.match(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', raw.strip())
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    return ""


def _clean_notice_title(row_text: str, pub_date: str) -> str:
    """Extract a notice title from raw row text by stripping known noise tokens."""
    t = row_text
    t = t.replace(pub_date, " ")
    for token in ["공지사항", "공지", "관리자"]:
        t = t.replace(token, " ")
    t = re.sub(r'조회수\s*\d+', " ", t)
    t = re.sub(r'^\s*\d+\s+', " ", t)  # leading row number
    t = re.sub(r'\s+', " ", t).strip()
    parts = [p.strip() for p in re.split(r'[\t\n/]', t) if p.strip() and len(p.strip()) > 3]
    if not parts:
        return ""
    return max(parts, key=len)[:300]


def _extract_mrmention_policy(html: str, url: str) -> Optional[dict]:
    """Extract 시행일 date from Mr. Mention policy/help page.

    Supports patterns:
      "시행일: 2025년 9월 22일"
      "시행일: 2025. 9. 22."
      "시행일: 2025-09-22"
    Returns dict with title='시행일 YYYY-MM-DD', published_at='YYYY-MM-DD'.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")

    # Korean year/month/day: "2025년 9월 22일"
    m = re.search(r'시행일[：:\s]*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', text)
    if m:
        date_str = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
        return {"title": f"시행일 {date_str}", "url": url, "published_at": date_str}

    # Numeric formats: "2025. 9. 22." / "2025-09-22" / "2025/09/22"
    m = re.search(r'시행일[：:\s]*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})', text)
    if m:
        date_str = _normalize_date(m.group(1))
        if date_str:
            return {"title": f"시행일 {date_str}", "url": url, "published_at": date_str}

    return None


async def _playwright_fetch_notice_generic(listing_url: str, competitor: str) -> tuple:
    """Generic Playwright-based notice list extractor for JS-rendered pages.

    Tries multiple DOM strategies to find rows containing dates, picks the most
    recent entry, then navigates to its detail page.

    Returns (post, detail_body, listing_html, candidates).
    """
    _DATE_RE = re.compile(r'\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}')
    tag = competitor.upper()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_UA,
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        listing_html: Optional[str] = None
        candidates: list[str] = []

        try:
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3_000)
            listing_html = await page.content()

            async def _collect_rows(locator) -> list[dict]:
                found = []
                try:
                    total = await locator.count()
                except Exception:
                    return []
                for i in range(min(total, 50)):
                    try:
                        row = locator.nth(i)
                        row_text = (await row.inner_text(timeout=3_000)).strip()
                        if not row_text or len(row_text) < 5:
                            continue
                        # Use the LAST date in the row as published_at.
                        # Notice rows often contain a policy-effective date inside
                        # the title (e.g. "(2026/05/06)") followed by the actual
                        # row-published date on the far right ("2026.04.29").
                        # findall + [-1] picks the rightmost/last date, not the first.
                        all_date_strs = _DATE_RE.findall(row_text)
                        if not all_date_strs:
                            continue
                        pub_date_raw = all_date_strs[-1]
                        pub_date = _normalize_date(pub_date_raw)
                        if not pub_date:
                            continue

                        title = ""
                        href = ""
                        links = row.get_by_role("link")
                        if await links.count() > 0:
                            link_text = (await links.first.inner_text(timeout=2_000)).strip()
                            if link_text and len(link_text) > 3:
                                title = link_text[:300]
                            href = (await links.first.get_attribute("href", timeout=2_000)) or ""

                        if not title:
                            title = _clean_notice_title(row_text, pub_date_raw)

                        if competitor == "zigbang" and i < 5:
                            print(
                                f"  [ZIGBANG_DEBUG] row={i}"
                                f" raw_text={row_text!r}"
                                f" extracted_title={title!r}"
                                f" extracted_published_date={pub_date!r}"
                            )

                        if href and not href.startswith("http"):
                            href = urljoin(listing_url, href)

                        if title or href:
                            found.append({
                                "title": title,
                                "url": href,
                                "published_at": pub_date,
                                "_date_key": pub_date.replace("-", ""),
                            })
                    except Exception:
                        continue
                return found

            strategies = [
                ("table tbody tr",   page.locator("table tbody tr")),
                ("[role=row]",       page.locator("[role='row']:not([role='columnheader'])")),
                ("listitem w/date",  page.locator("[role='listitem'], li").filter(
                                         has_text=_DATE_RE)),
                ("main div w/date",  page.locator("main div").filter(has_text=_DATE_RE)),
                ("div w/date (any)", page.locator("div").filter(has_text=_DATE_RE)),
            ]

            posts_found: list[dict] = []
            used_strategy = ""
            for name, locator in strategies:
                rows = await _collect_rows(locator)
                if rows:
                    posts_found = rows
                    used_strategy = name
                    break

            print(f"  [{tag}] strategy='{used_strategy}' candidates={len(posts_found)}")
            for entry in posts_found[:3]:
                print(f"  [{tag}]   {entry['published_at']}: {entry['title'][:80]}")

            if not posts_found:
                return None, "", listing_html, ["no_rows_with_date_found"]

            posts_found.sort(key=lambda x: x["_date_key"], reverse=True)
            post = {k: v for k, v in posts_found[0].items() if k != "_date_key"}
            candidates = [f"{e['published_at']}: {e['title'][:80]}" for e in posts_found[:5]]

            # Fetch detail page
            detail_body = ""
            href = post.get("url", "")
            if href:
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(1_500)
                    detail_html = await page.content()
                    detail_body = _extract_body_text(detail_html)
                    if not detail_body:
                        candidates.append("detail_navigation_failed: no body text extracted")
                except Exception as exc:
                    print(f"  [warn] {tag} detail failed: {exc}", file=sys.stderr)
                    candidates.append(f"detail_navigation_failed: {exc}")

            return post, detail_body, listing_html, candidates

        except Exception as exc:
            print(f"  [warn] Playwright fetch failed {listing_url}: {exc}", file=sys.stderr)
            return None, "", listing_html, []
        finally:
            await browser.close()


def _parse_liveanywhere_notices_bs4(html: str, listing_url: str) -> list:
    """Extract individual notice posts from LiveAnywhere listing HTML via BS4.

    Each post row is a <ul> containing a <a href*="bmode=view"> title link
    and a <li> with a plain YYYY-MM-DD date string.
    """
    _date_exact = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    soup = BeautifulSoup(html, "lxml")
    posts = []
    seen: set = set()

    for a_tag in soup.find_all("a", href=lambda h: h and "bmode=view" in h):
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 3:
            continue  # skip empty-text full-row mobile links

        href = a_tag.get("href", "")
        if not href.startswith("http"):
            href = urljoin(listing_url, href)
        if href in seen:
            continue
        seen.add(href)

        # Date is in a sibling <li> of the parent <ul>
        pub_date = ""
        ul = a_tag.find_parent("ul")
        if ul:
            for li in ul.find_all("li", recursive=False):
                li_text = li.get_text(strip=True)
                if _date_exact.match(li_text):
                    pub_date = li_text
                    break

        if not pub_date:
            continue

        posts.append({
            "title": title[:300],
            "url": href,
            "published_at": pub_date,
            "_date_key": pub_date.replace("-", ""),
        })

    posts.sort(key=lambda x: x["_date_key"], reverse=True)
    return posts


async def _playwright_fetch_liveanywhere(listing_url: str) -> tuple:
    """Fetch LiveAnywhere JS-rendered notice listing and detail page.

    Tries extraction strategies in order until one yields rows with dates:
      1. table tbody tr
      2. [role="row"] (ARIA table rows)
      3. main listitem  (fallback for list-style layouts)
      4. main div filtered by date pattern

    Within each strategy only rows that contain a YYYY-MM-DD date are kept.
    Rows are sorted descending by date; the latest is selected.
    Detail body is filtered to meaningful lines (>15 chars).

    Returns (post_dict_or_None, detail_body, listing_html_or_None, candidates).
    candidates holds "date: title" strings for diagnostics.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_UA,
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        listing_html: Optional[str] = None
        candidates: list[str] = []

        try:
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=90_000)

            # Soft networkidle wait — gives JS time to finish XHR after shell loads.
            # Timeout is ignored; we continue regardless.
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass

            # Wait for the exact anchor element BS4 will parse.
            # Fires the moment the element appears — no polling delay.
            notice_visible = False
            try:
                await page.wait_for_selector('a[href*="bmode=view"]', timeout=20_000)
                notice_visible = True
            except Exception:
                pass

            if not notice_visible:
                # BS4 fallback on partial HTML before declaring timeout.
                _partial_html = await page.content()
                _bs4_early = _parse_liveanywhere_notices_bs4(_partial_html, listing_url)
                if _bs4_early:
                    print(
                        f"  [LiveAnywhere] selector timed out but BS4 found {len(_bs4_early)} posts — continuing",
                        file=sys.stderr,
                    )
                    listing_html = _partial_html
                else:
                    print(
                        f"  [WARN] LiveAnywhere notice list not visible after selector wait",
                        file=sys.stderr,
                    )
                    return None, "", None, ["listing_timeout"]

            # Click '전체' tab to show all notices
            try:
                tabs = page.get_by_text("전체", exact=True)
                if await tabs.count() > 0:
                    await tabs.first.click()
                    await page.wait_for_timeout(1_500)
            except Exception:
                pass

            listing_html = await page.content()

            # --- Primary: BS4 bmode=view link extraction ---
            posts_found = _parse_liveanywhere_notices_bs4(listing_html, listing_url)
            used_strategy = "bs4 bmode=view" if posts_found else ""

            if posts_found:
                print(f"  [LiveAnywhere] strategy='{used_strategy}' candidates={len(posts_found)}")
                for entry in posts_found[:3]:
                    print(f"  [LiveAnywhere]   {entry['published_at']}: {entry['title'][:80]}")
            else:
                # --- Fallback: Playwright DOM strategies ---
                _date_re = re.compile(r'\d{4}-\d{2}-\d{2}')

            # Fallback Playwright strategies (only runs if BS4 found nothing)
            async def _collect_rows(locator) -> list[dict]:
                found = []
                total = await locator.count()
                for i in range(total):
                    try:
                        row = locator.nth(i)
                        row_text = await row.inner_text(timeout=3_000)
                        # Skip container-level elements whose text is too large to be
                        # a single notice row (e.g. the outer div containing all posts).
                        if len(row_text) > 500:
                            continue
                        dm = _date_re.search(row_text)
                        if not dm:
                            continue
                        pub_date = dm.group(0)
                        date_key = pub_date.replace("-", "")

                        # Prefer link text as title (clean and unambiguous)
                        title = ""
                        href = ""
                        links = row.get_by_role("link")
                        if await links.count() > 0:
                            link_text = (await links.first.inner_text(timeout=2_000)).strip()
                            if link_text and len(link_text) > 3:
                                title = link_text[:300]
                            href = (await links.first.get_attribute("href", timeout=2_000)) or ""

                        if not title:
                            title = _clean_notice_title(row_text, pub_date)

                        if href and not href.startswith("http"):
                            href = urljoin(listing_url, href)

                        # Skip navigation/filter links that resolve back to the listing page
                        if href == listing_url:
                            href = ""

                        if title or href:
                            found.append({
                                "title": title, "url": href,
                                "published_at": pub_date, "_date_key": date_key,
                            })
                    except Exception:
                        continue
                return found

            if not posts_found:
                strategies = [
                    ("table tbody tr",    page.locator("table tbody tr")),
                    ("[role=row]",        page.locator("[role='row']:not([role='columnheader'])")),
                    ("main listitem",     page.locator("main").get_by_role("listitem")),
                    ("main div w/date",   page.locator("main div").filter(
                                              has_text=re.compile(r'\d{4}-\d{2}-\d{2}'))),
                ]
                for name, locator in strategies:
                    rows = await _collect_rows(locator)
                    if rows:
                        posts_found = rows
                        used_strategy = name
                        break
                # Debug logging for Playwright fallback
                print(f"  [LiveAnywhere] strategy='{used_strategy}' candidates={len(posts_found)}")
                for entry in posts_found[:3]:
                    print(f"  [LiveAnywhere]   {entry['published_at']}: {entry['title'][:80]}")

            if not posts_found:
                return None, "", listing_html, candidates

            posts_found.sort(key=lambda x: x["_date_key"], reverse=True)
            post = {k: v for k, v in posts_found[0].items() if k != "_date_key"}
            candidates = [f"{e['published_at']}: {e['title'][:80]}" for e in posts_found[:5]]

            # --- Detail page ---
            detail_body = ""
            href = post.get("url", "")
            if not href:
                candidates.append("no_detail_link_found")
                return post, "", listing_html, candidates

            try:
                await page.goto(href, wait_until="load", timeout=60_000)
                await page.wait_for_timeout(1_500)
                for sel in ["article", "main", "[class*='content']", "[class*='post']"]:
                    try:
                        body_text = await page.locator(sel).first.inner_text(timeout=5_000)
                        meaningful = [
                            l.strip() for l in body_text.splitlines()
                            if l.strip() and len(l.strip()) > 15
                        ]
                        if meaningful:
                            detail_body = " ".join(meaningful)[:1000]
                            break
                    except Exception:
                        continue
                if not detail_body:
                    candidates.append("detail_navigation_failed: no body text extracted")
            except Exception as exc:
                print(f"  [warn] LiveAnywhere detail failed: {exc}", file=sys.stderr)
                candidates.append(f"detail_navigation_failed: {exc}")
                return post, "", listing_html, candidates

            return post, detail_body, listing_html, candidates

        except Exception as exc:
            print(f"  [warn] Playwright LiveAnywhere listing failed: {exc}", file=sys.stderr)
            return None, "", listing_html, candidates
        finally:
            await browser.close()


# ── Post extraction by page type (BS4) ───────────────────────────────────────

def _extract_news_list(soup: BeautifulSoup, base_url: str) -> Optional[dict]:
    for article in soup.select("article, .post, .entry, .article"):
        title_el = article.select_one("h1, h2, h3, h4, .entry-title, .post-title")
        link_el = article.select_one("a[href]")
        date_el = article.select_one("time, .date, .published, [datetime]")

        title = title_el.get_text(strip=True) if title_el else ""
        href = (link_el.get("href") or "") if link_el else ""
        date = (
            (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else ""
        )

        if title or href:
            if href and not href.startswith("http"):
                href = urljoin(base_url, href)
            return {"title": title[:300], "url": href[:500], "published_at": date[:50]}

    return None


def _extract_notice_list(soup: BeautifulSoup, base_url: str) -> Optional[dict]:
    candidates = [
        "ul.notice-list li:first-child",
        "table.board-list tr:nth-child(2)",
        ".notice-item:first-child",
        ".board-item:first-child",
        "li.post:first-child",
        ".post-list li:first-child",
        ".list-item:first-child",
        "li:first-child",
    ]
    for sel in candidates:
        item = soup.select_one(sel)
        if not item:
            continue
        title_el = item.select_one("a, .title, .subject")
        date_el = item.select_one("time, .date, .created-at, .datetime")

        title = title_el.get_text(strip=True) if title_el else ""
        href = ""
        if title_el and title_el.name == "a":
            href = title_el.get("href", "")
        if not href:
            link = item.select_one("a[href]")
            href = (link.get("href") or "") if link else ""
        date = date_el.get_text(strip=True) if date_el else ""

        if title or href:
            if href and not href.startswith("http"):
                href = urljoin(base_url, href)
            return {"title": title[:300], "url": href[:500], "published_at": date[:50]}

    for link in soup.select("main a[href], .content a[href], #content a[href]"):
        text = link.get_text(strip=True)
        href = link.get("href", "")
        if text and len(text) > 5 and href:
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            return {"title": text[:300], "url": href[:500], "published_at": ""}

    return None


def _extract_zendesk(soup: BeautifulSoup, base_url: str) -> Optional[dict]:
    for sel in [
        ".article-list-item a",
        ".article-list .article a",
        ".section-articles li a",
        "[class*='article'] a",
        ".blocks-item a",
    ]:
        link = soup.select_one(sel)
        if link:
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if title and href:
                if not href.startswith("http"):
                    href = urljoin(base_url, href)
                return {"title": title[:300], "url": href[:500], "published_at": ""}

    return None


def _extract_latest_post(html: str, page_type: str, source_url: str) -> Optional[dict]:
    if page_type == "mrmention_policy":
        return _extract_mrmention_policy(html, source_url)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    if page_type == "news_list":
        return _extract_news_list(soup, source_url)
    elif page_type == "zendesk":
        return _extract_zendesk(soup, source_url)
    else:
        return _extract_notice_list(soup, source_url)


def _check_image_post(post: dict, html: str) -> tuple:
    if not post or not html:
        return "", "ok"
    title = post.get("title", "")
    if title and re.match(r'^[\w\s\-\.]+\.(jpg|jpeg|png|gif)$', title, re.I):
        soup = BeautifulSoup(html, "lxml")
        img = soup.select_one("img[src]")
        if img:
            return img.get("src", ""), "image_needs_review"
    return "", "ok"


# ── Main job ─────────────────────────────────────────────────────────────────

def run() -> dict:
    """Check all notice board pages and write to policy_updates.

    Returns stats: {checked, new_count, changed_count, failed, results}
    """
    try:
        with open(_CONFIG_PATH) as f:
            policy_pages = yaml.safe_load(f)["policy_pages"]
    except (FileNotFoundError, KeyError) as exc:
        print(f"[fatal] could not load {_CONFIG_PATH}: {exc}", file=sys.stderr)
        sys.exit(1)

    ensure_headers("policy_updates")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    checked = 0
    new_count = 0
    changed_count = 0
    failed = 0
    results = []

    for competitor, pages in policy_pages.items():
        for page_cfg in pages:
            key = page_cfg["key"]
            url = page_cfg["url"]
            label = page_cfg["label"]
            page_type = page_cfg.get("type", "notice_list")
            display = page_cfg.get("competitor_display", competitor)
            may_have_images = page_cfg.get("may_have_image_posts", False)
            js_rendered = page_cfg.get("js_rendered", False)

            print(f"[POLICY] {competitor} / {label}")
            checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # --- Fetch listing ---
            html: Optional[str] = None
            post: Optional[dict] = None
            raw_text = ""
            candidates: list[str] = []
            detail_nav_failed = False
            post_from_playwright = False  # True when post+detail already populated

            if js_rendered and competitor == "liveanywhere":
                post, raw_text, html, candidates = asyncio.run(
                    _playwright_fetch_liveanywhere(url)
                )
                if html is None:
                    print(f"  [LiveAnywhere] 1차 실패, 재시도 중…", file=sys.stderr)
                    post, raw_text, html, candidates = asyncio.run(
                        _playwright_fetch_liveanywhere(url)
                    )
                post_from_playwright = True
                if post and any("detail_navigation_failed" in c for c in candidates):
                    detail_nav_failed = True
            elif js_rendered and page_type == "notice_list":
                # Generic Playwright extractor for JS-rendered notice lists (zigbang, 33m2)
                post, raw_text, html, candidates = asyncio.run(
                    _playwright_fetch_notice_generic(url, competitor)
                )
                post_from_playwright = True
                if post and any("detail_navigation_failed" in c for c in candidates):
                    detail_nav_failed = True
            elif js_rendered:
                # mrmention_policy + zendesk: fetch HTML first, extract via BS4 later
                # Try requests first; fall back to Playwright on failure
                html = _fetch_page(url)
                if html is None:
                    html = asyncio.run(_playwright_get_html(url))
                post = None
                raw_text = ""
            else:
                html = _fetch_page(url)
                post = None
                raw_text = ""

            # --- Fetch failure ---
            if html is None:
                failed += 1
                checked += 1
                is_timeout = "listing_timeout" in candidates
                sheet_status = "timeout" if is_timeout else "fetch_failed"
                error_msg = (
                    "liveanywhere_notice_list_timeout" if is_timeout
                    else f"fetch_failed: {url}"
                )
                summary = (
                    f"{display}: 공지사항 목록 로드 시간 초과" if is_timeout
                    else f"{display}: 페이지 수집 실패"
                )
                append_row("policy_updates", {
                    "date": today, "checked_at": checked_at,
                    "competitor": competitor, "source_page": key,
                    "latest_title": "", "latest_url": "", "latest_published_at": "",
                    "is_new": False, "is_changed": False,
                    "summary_ko": summary,
                    "raw_text": "", "image_text_ocr": "", "image_url": "",
                    "status": sheet_status,
                    "error_message": error_msg,
                })
                results.append({
                    "competitor": competitor, "key": key,
                    "status": sheet_status, "competitor_display": display,
                })
                continue

            checked += 1

            # --- Extract post ---
            if not post_from_playwright:
                post = _extract_latest_post(html, page_type, url)

            # --- No post extracted ---
            if post is None:
                print(
                    f"  [WARN] No post extracted from {url}",
                    file=sys.stderr,
                )
                failed += 1
                cand_str = " | candidates: " + str(candidates) if candidates else ""
                append_row("policy_updates", {
                    "date": today, "checked_at": checked_at,
                    "competitor": competitor, "source_page": key,
                    "latest_title": "", "latest_url": "", "latest_published_at": "",
                    "is_new": False, "is_changed": False,
                    "summary_ko": f"{display}: 게시물 파싱 실패",
                    "raw_text": "", "image_text_ocr": "", "image_url": "",
                    "status": "failed",
                    "error_message": f"no_post_extracted{cand_str}",
                })
                results.append({
                    "competitor": competitor, "key": key,
                    "status": "no_post", "competitor_display": display,
                })
                continue

            # --- Fetch detail body ---
            if post_from_playwright:
                pass  # detail already fetched by the Playwright extractor
            elif page_type == "mrmention_policy":
                raw_text = post.get("published_at", "")  # no separate detail page
            elif js_rendered:
                # Encostay (zendesk): fetch detail via Playwright
                detail_html = asyncio.run(_playwright_get_html(post.get("url", "")))
                if detail_html:
                    raw_text = _extract_body_text(detail_html)
                else:
                    raw_text = _fetch_detail_body(post.get("url", ""))
                    if not raw_text:
                        detail_nav_failed = True
            else:
                raw_text = _fetch_detail_body(post.get("url", ""))

            # --- Image check ---
            image_url, img_status = "", "ok"
            if may_have_images:
                image_url, img_status = _check_image_post(post, html)

            # --- Determine final status ---
            if detail_nav_failed:
                final_status = "detail_navigation_failed"
            elif img_status != "ok":
                final_status = img_status
            else:
                final_status = "ok"

            # --- Snapshot comparison ---
            snapshot = _load_snapshot(competitor, key)
            latest_url = post.get("url", "")
            latest_title = post.get("title", "")
            latest_published_at = post.get("published_at", "")
            is_new = False
            is_changed = False
            summary_ko = ""

            if snapshot is None:
                print(f"  [FIRST] {competitor}/{key}: initial snapshot saved")
                summary_ko = f"{display}: 최초 수집 완료 — \"{latest_title[:50]}\""
            else:
                prev_url = snapshot.get("latest_url", "")
                prev_title = snapshot.get("latest_title", "")

                if latest_url and latest_url != prev_url:
                    is_new = True
                    new_count += 1
                    print(f"  [NEW] {competitor}/{key}: 새 게시물 — \"{latest_title}\"")
                    summary_ko = f"{display}: 새 공지사항 — \"{latest_title[:50]}\""
                elif latest_title and latest_title != prev_title:
                    is_changed = True
                    changed_count += 1
                    print(f"  [CHANGED] {competitor}/{key}: 제목 변경")
                    summary_ko = f"{display}: 최신 게시물 변경 — \"{latest_title[:50]}\""
                else:
                    print(f"  [OK] {competitor}/{key}: 변경 없음")
                    summary_ko = f"{display}: 변경 없음"

            if final_status == "detail_navigation_failed":
                summary_ko += " (상세 페이지 이동 실패)"

            _save_snapshot(competitor, key, {
                "latest_url": latest_url,
                "latest_title": latest_title,
                "latest_published_at": latest_published_at,
                "checked_at": checked_at,
            })

            cand_str = " | candidates: " + str(candidates) if candidates and final_status != "ok" else ""
            append_row("policy_updates", {
                "date": today, "checked_at": checked_at,
                "competitor": competitor, "source_page": key,
                "latest_title": latest_title,
                "latest_url": latest_url,
                "latest_published_at": latest_published_at,
                "is_new": is_new,
                "is_changed": is_changed,
                "summary_ko": summary_ko,
                "raw_text": raw_text,
                "image_text_ocr": "",
                "image_url": image_url,
                "status": final_status,
                "error_message": (
                    f"detail_navigation_failed{cand_str}" if detail_nav_failed else ""
                ),
            })

            results.append({
                "competitor": competitor,
                "key": key,
                "is_new": is_new,
                "is_changed": is_changed,
                "title": latest_title,
                "status": final_status,
                "competitor_display": display,
            })

    print(f"\n[POLICY] Done. checked={checked}, new={new_count}, changed={changed_count}, failed={failed}")
    return {
        "checked": checked,
        "new_count": new_count,
        "changed_count": changed_count,
        "failed": failed,
        "results": results,
    }


if __name__ == "__main__":
    run()
