"""Collect iOS and Android app version info for all configured competitors.

Usage:
    python -m src.jobs.collect_app_versions                          # all
    python -m src.jobs.collect_app_versions --competitor 33m2        # one competitor
    python -m src.jobs.collect_app_versions --competitor 33m2 --platform android

Reads config/app_sources.yaml. For each competitor:
  - If ios_app_id is set:      iTunes Lookup API (no auth required)
  - If android_package is set: Playwright Play Store scraper

Change detection:
  Compares with the most recent previous successful row per (competitor, platform).
  - is_new_version: TRUE when version string changed
  - is_changed:     TRUE when version, release_date, or release_notes changed
  - First collection: both FALSE, change_summary_ko = "기준 스냅샷 저장"

Row schema (mirrors sheet_schema.yaml app_versions):
  date, checked_at, competitor, platform, app_name, app_id, package_name,
  version, release_date, release_notes, store_url,
  is_new_version, is_changed, change_summary_ko, status, error_message

Returns stats dict for run_daily.py: {checked, failed, results}
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import yaml
from dotenv import load_dotenv

from src.collectors.apps import itunes_lookup
from src.collectors.apps import google_play
from src.integrations.google_sheets import append_row, ensure_headers, read_sheet_rows

load_dotenv()

_CONFIG_PATH = Path(__file__).parents[2] / "config" / "app_sources.yaml"
_TAB = "app_versions"
_REQUEST_DELAY_SEC = 2


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)["app_sources"]


def _load_previous_map() -> dict:
    """Return {(competitor, platform): last_successful_row} from existing sheet rows."""
    rows = read_sheet_rows(_TAB)
    previous: dict[tuple, dict] = {}
    for row in rows:
        comp = row.get("competitor", "")
        plat = row.get("platform", "")
        if comp and plat and row.get("status") == "ok":
            previous[(comp, plat)] = row  # later rows overwrite earlier → last wins
    return previous


def _compute_change(result: dict, prev: Optional[dict]) -> Tuple[bool, bool, str]:
    """Return (is_new_version, is_changed, change_summary_ko).

    Args:
        result: current collection result dict
        prev:   previous sheet row dict, or None for first collection
    """
    if prev is None:
        return False, False, "기준 스냅샷 저장"

    new_ver = result.get("version", "")
    prev_ver = prev.get("version", "")
    new_date = result.get("release_date", "")
    prev_date = prev.get("release_date", "")
    new_notes = result.get("release_notes", "")
    prev_notes = prev.get("release_notes", "")

    parts = []
    is_new_version = False
    is_changed = False

    if new_ver != prev_ver:
        is_new_version = True
        is_changed = True
        parts.append(f"버전 변경: {prev_ver} -> {new_ver}")

    if new_date != prev_date:
        is_changed = True
        parts.append(f"업데이트 날짜 변경: {prev_date} -> {new_date}")

    if new_notes != prev_notes:
        is_changed = True
        parts.append("릴리즈 노트 변경 감지")

    change_summary_ko = "; ".join(parts) if parts else ""
    return is_new_version, is_changed, change_summary_ko


def run(
    competitor_filter: Optional[str] = None,
    platform_filter: Optional[str] = None,
) -> dict:
    """Run app version collection for all configured competitors/platforms.

    Args:
        competitor_filter: if set, collect only this competitor key (e.g. "33m2")
        platform_filter:   if set, collect only "ios" or "android"

    Returns:
        checked: number of (competitor, platform) rows with status != failed
        failed:  number of rows where status == failed
        results: list per row for run_daily Chat summary
    """
    app_sources = _load_config()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ensure_headers(_TAB)
    previous_map = _load_previous_map()

    checked = 0
    failed = 0
    results = []

    for competitor_key, cfg in app_sources.items():
        if competitor_filter and competitor_key != competitor_filter:
            continue

        display_name = cfg.get("display_name", competitor_key)
        ios_app_id = cfg.get("ios_app_id")
        android_package = cfg.get("android_package")
        ios_store_url = cfg.get("ios_store_url", "")
        android_store_url = cfg.get("android_store_url", "")

        print(f"\n[APP] {display_name}")

        # ── iOS ──────────────────────────────────────────────────────────────
        if ios_app_id and (platform_filter is None or platform_filter == "ios"):
            result = itunes_lookup.collect(app_id=ios_app_id, country="kr")
            status = result["status"]
            prev = previous_map.get((competitor_key, "ios"))

            if status == "ok":
                is_new_version, is_changed, change_summary_ko = _compute_change(result, prev)
            else:
                is_new_version, is_changed, change_summary_ko = False, False, ""

            row = {
                "date": today,
                "checked_at": checked_at,
                "competitor": competitor_key,
                "platform": "ios",
                "app_name": result.get("app_name", ""),
                "app_id": ios_app_id,
                "package_name": result.get("bundle_id", ""),
                "version": result.get("version", ""),
                "release_date": result.get("release_date", ""),
                "release_notes": result.get("release_notes", ""),
                "store_url": ios_store_url,
                "is_new_version": "TRUE" if is_new_version else "FALSE",
                "is_changed": "TRUE" if is_changed else "FALSE",
                "change_summary_ko": change_summary_ko,
                "status": status,
                "error_message": result.get("error", "") or "",
            }
            if append_row(_TAB, row):
                if status == "failed":
                    failed += 1
                else:
                    checked += 1
                version_str = result.get("version", "")
                print(
                    f"  [APP] iOS {display_name}: status={status}"
                    f" version={version_str!r} is_new={is_new_version} change={change_summary_ko!r}"
                )
            else:
                print(f"  [WARN] Sheet write failed {competitor_key}/ios", file=sys.stderr)

            results.append({
                "competitor": competitor_key,
                "display_name": display_name,
                "platform": "ios",
                "status": status,
                "version": result.get("version", ""),
                "is_new_version": is_new_version,
                "is_changed": is_changed,
                "change_summary_ko": change_summary_ko,
            })
            time.sleep(_REQUEST_DELAY_SEC)

        # ── Android ──────────────────────────────────────────────────────────
        if android_package and (platform_filter is None or platform_filter == "android"):
            result = google_play.collect_sync(package_id=android_package)
            status = result["status"]
            prev = previous_map.get((competitor_key, "android"))

            if status == "ok":
                is_new_version, is_changed, change_summary_ko = _compute_change(result, prev)
            else:
                is_new_version, is_changed, change_summary_ko = False, False, ""

            row = {
                "date": today,
                "checked_at": checked_at,
                "competitor": competitor_key,
                "platform": "android",
                "app_name": result.get("app_name", ""),
                "app_id": "",
                "package_name": android_package,
                "version": result.get("version", ""),
                "release_date": result.get("release_date", ""),
                "release_notes": result.get("release_notes", ""),
                "store_url": android_store_url,
                "is_new_version": "TRUE" if is_new_version else "FALSE",
                "is_changed": "TRUE" if is_changed else "FALSE",
                "change_summary_ko": change_summary_ko,
                "status": status,
                "error_message": result.get("error", "") or "",
            }
            if append_row(_TAB, row):
                if status == "failed":
                    failed += 1
                else:
                    checked += 1
                version_str = result.get("version", "")
                print(
                    f"  [APP] Android {display_name}: status={status}"
                    f" version={version_str!r} is_new={is_new_version} change={change_summary_ko!r}"
                )
            else:
                print(f"  [WARN] Sheet write failed {competitor_key}/android", file=sys.stderr)

            results.append({
                "competitor": competitor_key,
                "display_name": display_name,
                "platform": "android",
                "status": status,
                "version": result.get("version", ""),
                "is_new_version": is_new_version,
                "is_changed": is_changed,
                "change_summary_ko": change_summary_ko,
            })
            time.sleep(_REQUEST_DELAY_SEC)

        ios_active = ios_app_id and (platform_filter is None or platform_filter == "ios")
        android_active = android_package and (platform_filter is None or platform_filter == "android")
        if not ios_active and not android_active:
            print(f"  [APP] No app IDs configured (or filtered out) for {display_name} — skipping")

    print(f"\n[APP] Done. {checked} row(s) written to '{_TAB}'. failed={failed}")
    return {"checked": checked, "failed": failed, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect app version info")
    parser.add_argument(
        "--competitor",
        default=None,
        help="Collect only this competitor key, e.g. 33m2",
    )
    parser.add_argument(
        "--platform",
        default=None,
        choices=["ios", "android"],
        help="Collect only ios or android",
    )
    args = parser.parse_args()
    run(competitor_filter=args.competitor, platform_filter=args.platform)
