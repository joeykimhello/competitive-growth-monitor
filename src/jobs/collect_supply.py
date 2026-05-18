"""Collect supply/listing count signals for all competitors and regions.

Usage:
    python -m src.jobs.collect_supply

Reads config/supply_sources.yaml for competitors, regions, and URLs.
For each competitor+region:
  - Calls the matching collector in src/collectors/supply/{competitor}.py
  - Appends one row to the raw_supply_snapshots sheet tab
  - Saves a debug HTML snapshot to data/snapshots/supply/

Row schema (mirrors sheet_schema.yaml raw_supply_snapshots):
  date, collected_at, competitor, source, region, metric_name,
  count, raw_count_text, collection_method, confidence, source_url,
  status, error_message

Returns a stats dict for run_daily.py: {checked, failed, results}.
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.collectors.supply import airbnb as _airbnb
from src.collectors.supply import encostay as _encostay
from src.collectors.supply import liveanywhere as _liveanywhere
from src.integrations.google_sheets import append_row, ensure_headers

load_dotenv()

_SUPPLY_CONFIG_PATH = Path(__file__).parents[2] / "config" / "supply_sources.yaml"
_TAB = "raw_supply_snapshots"
_REQUEST_DELAY_SEC = 2

_COLLECTORS = {
    "airbnb": _airbnb,
    "liveanywhere": _liveanywhere,
    "encostay": _encostay,
}


def _load_config() -> dict:
    with open(_SUPPLY_CONFIG_PATH) as f:
        return yaml.safe_load(f)["supply_sources"]


def run() -> dict:
    """Run supply collection for all configured competitors/regions.

    Returns:
        checked: number of rows successfully written to sheet
        failed:  number of regions where status='failed'
        results: list of {competitor, region, status, count} for Chat summary
    """
    supply_sources = _load_config()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ensure_headers(_TAB)

    checked = 0
    failed = 0
    results = []

    for competitor_key, cfg in supply_sources.items():
        display_name = cfg.get("display_name", competitor_key)
        collector_key = cfg.get("collector", competitor_key)
        collector = _COLLECTORS.get(collector_key)

        if collector is None:
            print(f"[WARN] No collector for '{collector_key}' — skipping", file=sys.stderr)
            continue

        print(f"\n[SUPPLY] {display_name}")

        for region_key, region_cfg in cfg.get("regions", {}).items():
            region_label = region_cfg.get("label", region_key)
            url = region_cfg["url"]
            source = region_cfg.get("source", competitor_key)
            metric_name = region_cfg.get("metric_name", "listing_count")
            collection_method = region_cfg.get("collection_method", "")
            confidence = region_cfg.get("confidence", "")

            try:
                result = collector.collect_sync(
                    competitor_key=competitor_key,
                    region_key=region_key,
                    region_label=region_label,
                    url=url,
                )
            except Exception as exc:
                result = {
                    "count": None,
                    "raw_count_text": "",
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }

            status = result.get("status", "failed")
            count = result.get("count")
            if status == "failed":
                failed += 1

            row = {
                "date": today,
                "collected_at": collected_at,
                "competitor": competitor_key,
                "source": source,
                "region": region_key,
                "metric_name": metric_name,
                "count": count,
                "raw_count_text": result.get("raw_count_text", ""),
                "collection_method": collection_method,
                "confidence": confidence,
                "source_url": url,
                "status": status,
                "error_message": result.get("error", "") or "",
            }

            written = append_row(_TAB, row)
            if written:
                checked += 1
                count_str = f"{count:,}" if count is not None else "null"
                print(f"  [{status.upper()}] {display_name} / {region_label}: count={count_str}")
            else:
                print(
                    f"  [WARN] Sheet write failed for {display_name} / {region_label}",
                    file=sys.stderr,
                )

            results.append({
                "competitor": competitor_key,
                "region": region_key,
                "status": status,
                "count": count,
            })

            time.sleep(_REQUEST_DELAY_SEC)

    print(f"\n[SUPPLY] Done. {checked} row(s) written to '{_TAB}'. failed={failed}")
    return {"checked": checked, "failed": failed, "results": results}


if __name__ == "__main__":
    run()
