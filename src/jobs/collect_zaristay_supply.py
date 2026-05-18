"""Collect Zaristay supply counts only (seoul + nationwide).

Usage:
    python -m src.jobs.collect_zaristay_supply

Reads zaristay config from config/supply_sources.yaml and appends rows to
raw_supply_snapshots. Useful for quick testing without running all competitors.
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.collectors.supply import zaristay as _zaristay
from src.integrations.google_sheets import append_row, ensure_headers

load_dotenv()

_SUPPLY_CONFIG_PATH = Path(__file__).parents[2] / "config" / "supply_sources.yaml"
_TAB = "raw_supply_snapshots"
_REQUEST_DELAY_SEC = 2


def run() -> dict:
    with open(_SUPPLY_CONFIG_PATH) as f:
        all_sources = yaml.safe_load(f)["supply_sources"]

    cfg = all_sources.get("zaristay")
    if cfg is None:
        print("[ERROR] 'zaristay' not found in supply_sources.yaml", file=sys.stderr)
        return {"checked": 0, "failed": 1, "results": []}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    display_name = cfg.get("display_name", "zaristay")

    ensure_headers(_TAB)

    checked = 0
    failed = 0
    results = []

    print(f"\n[SUPPLY] {display_name}")

    for region_key, region_cfg in cfg.get("regions", {}).items():
        region_label = region_cfg.get("label", region_key)
        url = region_cfg["url"]
        source = region_cfg.get("source", "zaristay")
        metric_name = region_cfg.get("metric_name", "listing_count")
        collection_method = region_cfg.get("collection_method", "")
        confidence = region_cfg.get("confidence", "")

        try:
            result = _zaristay.collect_sync(
                competitor_key="zaristay",
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
            "competitor": "zaristay",
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
            "competitor": "zaristay",
            "region": region_key,
            "status": status,
            "count": count,
        })

        time.sleep(_REQUEST_DELAY_SEC)

    print(f"\n[SUPPLY] Done. {checked} row(s) written to '{_TAB}'. failed={failed}")
    return {"checked": checked, "failed": failed, "results": results}


if __name__ == "__main__":
    run()
