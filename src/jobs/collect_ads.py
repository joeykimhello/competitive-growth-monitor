"""Collect ad counts and creatives from Meta Ad Library and Google Ads Transparency Center.

Usage:
    python -m src.jobs.collect_ads

For each competitor in config/competitors.yaml:
  - Meta Ad Library  → active_ad_count + creatives → daily_ad_counts + ad_creatives
  - Google Ads TC    → platform counts → daily_ad_counts (same row, google_* columns)

Each competitor gets exactly one row in daily_ad_counts per run.
Each valid Meta creative gets one row in ad_creatives.

Returns a stats dict consumed by run_daily.py.
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.collectors.ads import meta_ad_library, google_ads_transparency
from src.integrations.google_sheets import append_row, ensure_headers

load_dotenv()

_CONFIG_PATH = Path(__file__).parents[2] / "config" / "competitors.yaml"
_DELAY_SEC = 2


def _load_competitors() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)["competitors"]


def run() -> dict:
    """Run ad collection for all competitors.

    Returns stats:
        meta_checked, google_checked, failed, creative_rows_written, results
    """
    competitors = _load_competitors()
    ensure_headers("daily_ad_counts")
    ensure_headers("ad_creatives")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    meta_checked = 0
    google_checked = 0
    failed = 0
    creative_rows = 0
    results = []

    _failed_result = lambda status, err: {
        "active_ad_count": None, "creatives": [],
        "source_url": "", "status": status, "error": err,
    }
    _failed_google = lambda status, err: {
        "total_count": None, "google_search_count": None, "youtube_count": None,
        "google_maps_count": None, "google_play_count": None, "google_shopping_count": None,
        "source_url": "", "google_date_range": "", "selected_advertiser_name": None,
        "status": status, "error": err,
    }

    for key, cfg in competitors.items():
        display_name = cfg.get("display_name", key)
        print(f"\n[ADS] {display_name}")

        # ── Meta Ad Library ──────────────────────────────────────────────────
        meta_url = cfg.get("meta_ad_library_url")
        if meta_url:
            print(f"  [META] Collecting…")
            meta_result = meta_ad_library.collect_sync(
                competitor_key=key,
                display_name=display_name,
                meta_ad_library_url=meta_url,
            )
            if meta_result["status"] != "failed":
                meta_checked += 1
            else:
                failed += 1
        else:
            meta_result = _failed_result("failed", "not_configured")

        time.sleep(_DELAY_SEC)

        # ── Google Ads Transparency Center ───────────────────────────────────
        advertiser_name = cfg.get("google_ads_transparency_advertiser")
        search_terms = cfg.get("google_ads_transparency_search_terms") or (
            [advertiser_name] if advertiser_name else []
        )
        if advertiser_name:
            print(f"  [GOOGLE] Collecting… (terms: {search_terms})")
            google_result = google_ads_transparency.collect_sync(
                competitor_key=key,
                display_name=display_name,
                advertiser_name=advertiser_name,
                search_terms=search_terms,
                preferred_advertiser_name=advertiser_name,
            )
            g_status = google_result.get("status", "failed")
            if g_status not in ("failed",):
                google_checked += 1
            else:
                failed += 1
        else:
            google_result = _failed_google("failed", "not_configured")

        time.sleep(_DELAY_SEC)

        # ── Write daily_ad_counts row ─────────────────────────────────────────
        error_parts = []
        if meta_result.get("error"):
            error_parts.append(f"meta: {meta_result['error']}")
        # Google: pack selected advertiser + search term into error_message for diagnostics
        g_error = google_result.get("error")
        g_selected = google_result.get("selected_advertiser_name")
        g_term = google_result.get("search_term_used")
        if g_error:
            error_parts.append(f"google: {g_error}")
        elif g_selected:
            diag = f"selected: {g_selected}"
            if g_term:
                diag += f" (search: {g_term})"
            error_parts.append(f"google: {diag}")

        count_row = {
            "date": today,
            "collected_at": collected_at,
            "competitor": key,
            "meta_active_ads_count": meta_result.get("active_ad_count"),
            "google_total_ads_count": google_result.get("total_count"),
            "google_date_range": google_result.get("google_date_range", ""),
            "google_maps_count": google_result.get("google_maps_count"),
            "google_play_count": google_result.get("google_play_count"),
            "google_shopping_count": google_result.get("google_shopping_count"),
            "google_search_count": google_result.get("google_search_count"),
            "youtube_count": google_result.get("youtube_count"),
            "meta_status": meta_result.get("status", "failed"),
            "google_status": google_result.get("status", "failed"),
            "error_message": " | ".join(error_parts)[:500],
            "meta_source_url": meta_result.get("source_url", ""),
            "google_source_url": google_result.get("source_url", ""),
            "google_selected_advertiser_name": google_result.get("selected_advertiser_name", ""),
        }

        if append_row("daily_ad_counts", count_row):
            print(f"  [SHEET] daily_ad_counts written for {key}")
        else:
            print(f"  [WARN] daily_ad_counts write failed for {key}", file=sys.stderr)

        # ── Write ad_creatives rows (Meta only, deduplicated) ────────────────
        seen_creative_keys: set = set()
        written_count = 0
        for creative in meta_result.get("creatives", []):
            library_id = creative.get("library_id", "")
            creative_hash = creative.get("creative_hash", "")
            creative_text = creative.get("creative_text", "")
            landing_url = creative.get("landing_url", "")
            dedup_key = (
                library_id
                or creative_hash
                or f"{key}:{creative_text[:100]}:{landing_url}"
            )
            if not dedup_key or dedup_key in seen_creative_keys:
                continue
            seen_creative_keys.add(dedup_key)
            creative_row = {
                "date": today,
                "collected_at": collected_at,
                "competitor": key,
                "source": "meta_ad_library",
                "platform": "meta",
                "advertiser_name": creative.get("advertiser_name") or display_name,
                "library_id": library_id,
                "ad_detail_url": creative.get("ad_detail_url", ""),
                "ad_start_date": creative.get("ad_start_date", ""),
                "started_running_text": creative.get("started_running_text", ""),
                "platforms": creative.get("platforms", ""),
                "creative_text": creative_text,
                "landing_url": landing_url,
                "creative_type": creative.get("creative_type", "unknown"),
                "creative_hash": creative_hash,
                "source_url": meta_result.get("source_url", ""),
                "status": "ok",
                "error_message": "",
            }
            if append_row("ad_creatives", creative_row):
                creative_rows += 1
                written_count += 1

        if meta_result.get("creatives"):
            total_seen = len(meta_result["creatives"])
            dupes = total_seen - written_count
            dupe_str = f" ({dupes} dupes skipped)" if dupes else ""
            print(f"  [SHEET] ad_creatives: {written_count} rows written for {key}{dupe_str}")

        results.append({
            "competitor": key,
            "display_name": display_name,
            "meta_status": meta_result.get("status"),
            "google_status": google_result.get("status"),
            "meta_count": meta_result.get("active_ad_count"),
            "google_total": google_result.get("total_count"),
        })

    print(
        f"\n[ADS] Done. meta_checked={meta_checked}, google_checked={google_checked}, "
        f"failed={failed}, creatives_written={creative_rows}"
    )
    return {
        "meta_checked": meta_checked,
        "google_checked": google_checked,
        "failed": failed,
        "creative_rows": creative_rows,
        "results": results,
    }


if __name__ == "__main__":
    run()
