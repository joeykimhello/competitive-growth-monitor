"""33m2 supply collector — summed room count across 4 property-type groups.

33m2 does not expose a total count on a single URL. Instead we fetch 4 search
result pages (grouped by property type), extract the count from each, and sum
them to get the total count for the given region.

Pattern extracted from page text: ([\d,]+)\s*개의\s*검색결과

Supported regions:
  nationwide — 4 groups, no keyword filter
  seoul      — 4 groups, keyword=서울 prepended

Status semantics:
  ok:      all 4 groups collected successfully
  partial: at least 1 group collected, at least 1 failed
  failed:  all groups failed (count=None returned)

Per-group breakdown stored as JSON in raw_count_text:
  {"method": "sum_by_property_type_groups", "region": "...", "groups": [...], "total": N}

Snapshots saved to: data/snapshots/supply/33m2__{region}__{group}__{timestamp}.html

collect_sync() usage:
  # Standalone — runs both regions, returns list[SupplyResult]
  results = m33m2.collect_sync()

  # Via collect_supply.py — runs one region, returns SupplyResult
  result = m33m2.collect_sync(competitor_key, region_key, region_label, url)
  result.get("status")  # dict-like access still works
"""

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

_SNAPSHOT_DIR = Path(__file__).parents[3] / "data" / "snapshots" / "supply"
_PAGE_LOAD_TIMEOUT = 25_000
_COUNT_WAIT_TIMEOUT = 10_000
_BETWEEN_GROUP_DELAY_MS = 1_500

_BASE_URL = "https://web.33m2.co.kr/guest/room"
_COUNT_PATTERN = re.compile(r"([\d,]+)\s*개의\s*검색결과")

_NATIONWIDE_GROUPS: List[dict] = [
    {
        "name": "officetel_motel",
        "url": f"{_BASE_URL}?sort=POPULAR&propertyTypes=OFFICETEL%2CMOTEL",
    },
    {
        "name": "apartment_villa",
        "url": f"{_BASE_URL}?sort=POPULAR&propertyTypes=APARTMENT%2CVILLA",
    },
    {
        "name": "detached_studio",
        "url": f"{_BASE_URL}?sort=POPULAR&propertyTypes=DETACHED%2CSTUDIO",
    },
    {
        "name": "mixed_use_etc",
        "url": (
            f"{_BASE_URL}?sort=POPULAR"
            "&propertyTypes=MIXED_USE%2CGOSIWON%2CHOTEL%2CSHARE_HOUSE%2CVACATION_HOME%2CGUEST_HOUSE"
        ),
    },
]

_SEOUL_GROUPS: List[dict] = [
    {
        "name": "officetel_motel",
        "url": f"{_BASE_URL}?keyword=%EC%84%9C%EC%9A%B8&sort=POPULAR&propertyTypes=OFFICETEL%2CMOTEL",
    },
    {
        "name": "apartment_villa",
        "url": f"{_BASE_URL}?keyword=%EC%84%9C%EC%9A%B8&sort=POPULAR&propertyTypes=APARTMENT%2CVILLA",
    },
    {
        "name": "detached_studio",
        "url": f"{_BASE_URL}?keyword=%EC%84%9C%EC%9A%B8&sort=POPULAR&propertyTypes=DETACHED%2CSTUDIO",
    },
    {
        "name": "mixed_use_etc",
        "url": (
            f"{_BASE_URL}?keyword=%EC%84%9C%EC%9A%B8&sort=POPULAR"
            "&propertyTypes=GOSIWON%2CMIXED_USE%2CHOTEL%2CSHARE_HOUSE%2CVACATION_HOME%2CGUEST_HOUSE"
        ),
    },
]

_GROUPS_BY_REGION = {
    "nationwide": _NATIONWIDE_GROUPS,
    "seoul": _SEOUL_GROUPS,
}

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class SupplyResult:
    """Single-region result. Supports both attribute access and dict-like .get()."""

    competitor: str
    region: str
    status: str
    count: Optional[int]
    raw_count_text: str
    error: Optional[str] = None

    def get(self, key: str, default=None):
        """Dict-like access — keeps collect_supply.py working unchanged."""
        return getattr(self, key, default)


def _snapshot_path(region_key: str, group_name: str) -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _SNAPSHOT_DIR / f"33m2__{region_key}__{group_name}__{ts}.html"


async def _fetch_group_count(
    page, group: dict, region_key: str, save_snapshot: bool
) -> Optional[int]:
    """Load one property-type group URL and return the extracted listing count."""
    url = group["url"]
    name = group["name"]
    print(f"  [33m2/{region_key}] {name} — {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)

        try:
            await page.wait_for_function(
                "() => (document.body.innerText || '').includes('개의 검색결과')",
                timeout=_COUNT_WAIT_TIMEOUT,
            )
        except PlaywrightTimeoutError:
            print(
                f"  [33m2/{region_key}] {name}: wait_for_function timed out — reading current text",
                file=sys.stderr,
            )

        if save_snapshot:
            snap = _snapshot_path(region_key, name)
            snap.write_text(await page.content(), encoding="utf-8")
            print(f"  [33m2/{region_key}] snapshot: {snap.name}")

        text = (await page.evaluate("document.body.innerText") or "").strip()
        m = _COUNT_PATTERN.search(text)
        if m:
            count = int(m.group(1).replace(",", ""))
            print(f"  [33m2/{region_key}] {name}: {count:,} (raw: {m.group(0)!r})")
            return count

        print(
            f"  [33m2/{region_key}] {name}: count pattern not found (text_len={len(text)})",
            file=sys.stderr,
        )
        return None

    except Exception as exc:
        print(f"  [33m2/{region_key}] {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


async def _collect_region(
    competitor_key: str, region_key: str, region_label: str, save_snapshot: bool
) -> SupplyResult:
    """Fetch all 4 groups for one region and return a SupplyResult."""
    groups = _GROUPS_BY_REGION.get(region_key)
    if groups is None:
        return SupplyResult(
            competitor=competitor_key,
            region=region_key,
            status="failed",
            count=None,
            raw_count_text="",
            error=f"unknown_region: {region_key!r}",
        )

    group_results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        try:
            for i, group in enumerate(groups):
                count = await _fetch_group_count(page, group, region_key, save_snapshot)
                group_results.append(
                    {"name": group["name"], "url": group["url"], "count": count}
                )
                if i < len(groups) - 1:
                    await page.wait_for_timeout(_BETWEEN_GROUP_DELAY_MS)
        finally:
            await browser.close()

    success = [r for r in group_results if r["count"] is not None]
    failed_names = [r["name"] for r in group_results if r["count"] is None]

    raw_payload = {
        "method": "sum_by_property_type_groups",
        "region": region_key,
        "groups": group_results,
        "total": None,
    }

    if not success:
        return SupplyResult(
            competitor=competitor_key,
            region=region_key,
            status="failed",
            count=None,
            raw_count_text=json.dumps(raw_payload, ensure_ascii=False),
            error=f"all_groups_failed: {failed_names}",
        )

    total = sum(r["count"] for r in success)
    raw_payload["total"] = total
    status = "ok" if not failed_names else "partial"
    error = f"failed_groups: {failed_names}" if failed_names else None

    print(
        f"  [33m2/{region_key}] total={total:,} status={status}"
        + (f" failed={failed_names}" if failed_names else "")
    )

    return SupplyResult(
        competitor=competitor_key,
        region=region_key,
        status=status,
        count=total,
        raw_count_text=json.dumps(raw_payload, ensure_ascii=False),
        error=error,
    )


def collect_sync(
    competitor_key: str = "33m2",
    region_key: Optional[str] = None,
    region_label: str = "",
    url: str = "",
    save_snapshot: bool = True,
) -> Union[List[SupplyResult], SupplyResult]:
    """Collect room counts for 33m2.

    No-arg / standalone mode (region_key=None):
        Returns list[SupplyResult] for all regions (nationwide + seoul).

    Single-region mode (called by collect_supply.py):
        Returns a single SupplyResult. Supports .get() for dict-like access.
    """
    if region_key is None:
        results = []
        for rk in ("nationwide", "seoul"):
            label = "nationwide" if rk == "nationwide" else "Seoul"
            result = asyncio.run(_collect_region(competitor_key, rk, label, save_snapshot))
            results.append(result)
        return results

    return asyncio.run(_collect_region(competitor_key, region_key, region_label, save_snapshot))
