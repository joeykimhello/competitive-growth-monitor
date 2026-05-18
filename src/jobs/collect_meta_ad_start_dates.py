"""Collect Meta Ad Library ad start dates for all competitors.

Usage:
    python -m src.jobs.collect_meta_ad_start_dates

For each competitor navigates to the fixed Meta Ad Library page URL and
extracts per-ad data:

  library_id          — from '라이브러리 ID: NNN' or 'Library ID: NNN' in card text
  started_running_text — full Korean date string (e.g. '2026. 5. 7.에 게재 시작함')
  ad_start_date       — YYYY-MM-DD parsed from started_running_text
  creative_text       — ad body text up to 500 chars
  landing_url         — cleaned landing URL

One row per ad per competitor is written to the meta_ad_start_dates sheet tab.
Within each run ads are deduplicated by library_id.
library_id is blank (and ad_detail_url is blank) when not found in card text.

Yellow highlight on ad_start_date:
  Cells where ad_start_date < (collected_date - 1 month) are highlighted yellow.
  These represent ads running for over a month — long-running creative review targets.
  # 한 달 초과 게재 중인 광고로, 장기 운영/성과 추정 소재 검토 대상

Extraction regexes (as specified):
  library_id:  r"라이브러리\\s*ID[:：]?\\s*(\\d+)|Library\\s*ID[:：]?\\s*(\\d+)"
  start date:  r"((\\d{4})\\.\\s*(\\d{1,2})\\.\\s*(\\d{1,2})\\.?\\s*에\\s*게재\\s*시작함)"

Status per row:
  ok            — library_id and ad_start_date both present
  missing_fields — one or both absent; error_message lists which fields
  failed        — collector-level failure

Returns stats dict: {checked, failed, written, results}
"""

import calendar
import re
import sys
from datetime import date, datetime, timezone

from dotenv import load_dotenv

from src.collectors.ads import meta_ad_library
from src.integrations.google_sheets import (
    append_row_get_index,
    clear_cell_background,
    color_cell_yellow,
    ensure_headers,
)

load_dotenv()

_TAB = "meta_ad_start_dates"

# 0-based column index of ad_start_date in meta_ad_start_dates schema:
# date(0) collected_at(1) competitor(2) advertiser_name(3) library_id(4)
# ad_detail_url(5) started_running_text(6) ad_start_date(7) ...
_AD_START_DATE_COL = 7

_COMPETITORS = {
    "airbnb": {
        "display_name": "Airbnb",
        "advertiser_name": "에어비앤비코리아",
        "url": (
            "https://www.facebook.com/ads/library/"
            "?active_status=active&ad_type=all&country=KR"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            "&view_all_page_id=324826532457"
        ),
    },
    "liveanywhere": {
        "display_name": "LiveAnywhere",
        "advertiser_name": "리브애니웨어",
        "url": (
            "https://www.facebook.com/ads/library/"
            "?active_status=active&ad_type=all&country=KR"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            "&view_all_page_id=352761898712591"
        ),
    },
    "encostay": {
        "display_name": "Encostay",
        "advertiser_name": "엔코스테이",
        "url": (
            "https://www.facebook.com/ads/library/"
            "?active_status=active&ad_type=all&country=KR"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            "&source=fb-logo&view_all_page_id=108631594606079"
        ),
    },
    "zaristay": {
        "display_name": "자리톡",
        "advertiser_name": "자리톡",
        "url": (
            "https://www.facebook.com/ads/library/"
            "?active_status=active&ad_type=all&country=KR"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            "&view_all_page_id=605506329321039"
        ),
    },
    "zigbang": {
        "display_name": "직방",
        "advertiser_name": "직방",
        "url": (
            "https://www.facebook.com/ads/library/"
            "?active_status=active&ad_type=all&country=KR"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            "&view_all_page_id=1539050752983356"
        ),
    },
    "mister_mention": {
        "display_name": "미스터멘션",
        "advertiser_name": "미스터멘션",
        "url": (
            "https://www.facebook.com/ads/library/"
            "?active_status=active&ad_type=all&country=KR"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            "&view_all_page_id=134701860224780"
        ),
    },
    "33m2": {
        "display_name": "삼삼엠투",
        "advertiser_name": "삼삼엠투",
        "url": (
            "https://www.facebook.com/ads/library/"
            "?active_status=active&ad_type=all&country=KR"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            "&view_all_page_id=532282707266733"
        ),
    },
}

# Extraction regexes — applied to full visible card text
_LIBRARY_ID_RE = re.compile(
    r"라이브러리\s*ID[:：]?\s*(\d+)|Library\s*ID[:：]?\s*(\d+)"
)
_STARTED_RE = re.compile(
    r"((\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*에\s*게재\s*시작함)"
)


def _extract_library_id(card_text: str) -> str:
    m = _LIBRARY_ID_RE.search(card_text)
    if m:
        return m.group(1) or m.group(2)
    return ""


def _extract_started(card_text: str) -> tuple[str, str]:
    """Return (started_running_text, ad_start_date YYYY-MM-DD). ('', '') on no match."""
    m = _STARTED_RE.search(card_text)
    if m:
        full_text = m.group(1)
        y, mo, d = m.group(2), m.group(3).zfill(2), m.group(4).zfill(2)
        return full_text, f"{y}-{mo}-{d}"
    return "", ""


def _one_month_ago(ref: date) -> date:
    """Return the date exactly one calendar month before ref.

    Examples:
      2026-05-18 → 2026-04-18
      2026-03-31 → 2026-02-28  (clamped to last day of Feb)
      2026-01-15 → 2025-12-15
    """
    month = ref.month - 1 or 12
    year = ref.year if ref.month > 1 else ref.year - 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(ref.day, max_day))


def _should_highlight(ad_start_date: str, collection_date: str) -> tuple[bool, str]:
    """Return (highlight, threshold_str).

    highlight is True only when ad_start_date < collection_date - 1 calendar month.

    Examples (collection_date = 2026-05-18, threshold = 2026-04-18):
      2026-05-14 → False   (recent)
      2026-04-18 → False   (exactly one month — not highlighted)
      2026-04-17 → True    (over one month)
    """
    if not ad_start_date or not collection_date:
        return False, ""
    try:
        start = date.fromisoformat(ad_start_date)
        ref = date.fromisoformat(collection_date)
        threshold = _one_month_ago(ref)
        return start < threshold, str(threshold)
    except Exception:
        return False, ""


def run() -> dict:
    """Run Meta ad start date collection for all configured competitors.

    Returns:
        checked: number of competitors attempted
        failed:  number of competitors where collector returned status=failed
        written: total sheet rows written
        results: list per competitor with status and row counts
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ensure_headers(_TAB)

    total_written = 0
    total_failed = 0
    results = []
    debug_rows_logged = 0  # print highlight debug for first 10 rows across all competitors

    for competitor_key, cfg in _COMPETITORS.items():
        display_name = cfg["display_name"]
        source_url = cfg["url"]
        fallback_advertiser = cfg["advertiser_name"]

        print(f"\n[META_DATES] {display_name}")

        collector_result = meta_ad_library.collect_sync(
            competitor_key=competitor_key,
            display_name=display_name,
            meta_ad_library_url=source_url,
        )

        # Page-level displayed count (e.g. "결과 ~80개" → 80)
        displayed_meta_count = collector_result.get("active_ad_count")
        displayed_count_raw = collector_result.get("active_ad_count_raw", "")

        if collector_result["status"] == "failed":
            total_failed += 1
            results.append({
                "competitor": competitor_key,
                "display_name": display_name,
                "status": "failed",
                "written": 0,
                "dupes": 0,
                "displayed_meta_count": None,
            })
            continue

        creatives = collector_result.get("creatives", [])
        seen_library_ids: set[str] = set()
        written = 0
        dupes = 0

        for creative in creatives:
            library_id = creative.get("library_id", "")
            started_running_text = creative.get("started_running_text", "")
            ad_start_date = creative.get("ad_start_date", "")

            # Dedup by library_id (skip blank-id deduplication — each blank is kept)
            if library_id:
                if library_id in seen_library_ids:
                    dupes += 1
                    continue
                seen_library_ids.add(library_id)

            ad_detail_url = (
                f"https://www.facebook.com/ads/library/?id={library_id}"
                if library_id else ""
            )

            if library_id and ad_start_date:
                row_status = "ok"
                error_msg = ""
            else:
                row_status = "missing_fields"
                missing = []
                if not library_id:
                    missing.append("library_id")
                if not ad_start_date:
                    missing.append("ad_start_date")
                error_msg = "missing: " + ", ".join(missing)

            row = {
                "date": today,
                "collected_at": collected_at,
                "competitor": competitor_key,
                "advertiser_name": creative.get("advertiser_name", "") or fallback_advertiser,
                "library_id": library_id,
                "ad_detail_url": ad_detail_url,
                "started_running_text": started_running_text,
                "ad_start_date": ad_start_date,
                "creative_text": creative.get("creative_text", ""),
                "landing_url": creative.get("landing_url", ""),
                "source_url": source_url,
                "status": row_status,
                "error_message": error_msg,
            }

            # Use the row's own date field as collection_date; fall back to today
            collection_date = row.get("date") or today
            highlight, threshold_str = _should_highlight(ad_start_date, collection_date)

            if debug_rows_logged < 10:
                print(
                    f"  [HIGHLIGHT_DEBUG] collection_date={collection_date}"
                    f" threshold={threshold_str}"
                    f" ad_start_date={ad_start_date!r}"
                    f" should_highlight={highlight}"
                )
                debug_rows_logged += 1

            row_index = append_row_get_index(_TAB, row)
            if row_index is not None:
                written += 1
                # INSERT_ROWS inherits formatting from the row above — always clear first
                clear_cell_background(_TAB, row_index, _AD_START_DATE_COL)
                # 한 달 초과 게재 중인 광고 — ad_start_date 셀만 노란색 표시
                if highlight:
                    color_cell_yellow(_TAB, row_index, _AD_START_DATE_COL)
            else:
                print(
                    f"  [WARN] Sheet write failed for {display_name} "
                    f"library_id={library_id or '(blank)'}",
                    file=sys.stderr,
                )

        print(
            f"  [META_DATES] {display_name}: {written} rows written, {dupes} dupes skipped"
        )
        print(
            f"  [META_COUNT_DEBUG] competitor={competitor_key}"
            f" raw={displayed_count_raw!r}"
            f" displayed_meta_count={displayed_meta_count}"
            f" written={written}"
        )
        total_written += written
        results.append({
            "competitor": competitor_key,
            "display_name": display_name,
            "status": collector_result["status"],
            "written": written,
            "dupes": dupes,
            "displayed_meta_count": displayed_meta_count,
        })

    print(
        f"\n[META_DATES] Done. {total_written} total rows written to '{_TAB}'. "
        f"failed={total_failed}"
    )
    return {
        "checked": len(_COMPETITORS),
        "failed": total_failed,
        "written": total_written,
        "results": results,
    }


if __name__ == "__main__":
    run()
