"""Daily run orchestrator.

Usage:
    python -m src.jobs.run_daily

Runs all four workflows in sequence:
  1. Ad collection — Meta Ad Library + Google Ads Transparency (collect_ads)
  2. Supply collection — listing/house counts (collect_supply)
  3. Policy / Notice board updates (detect_policy_changes)

After all workflows finish:
  - Writes one row to the run_log sheet tab
  - Sends a single Korean summary to Google Chat

All Google Chat messages are in Korean. No per-page English alerts are sent.
"""

import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.jobs import collect_meta_ad_start_dates, collect_supply, detect_policy_changes
from src.integrations.google_sheets import append_row, ensure_headers
from src.integrations.google_chat import send_google_chat_message

load_dotenv()

# Fixed display order for [Meta 광고] — 7 competitors
_META_ORDER = [
    ("airbnb",         "Airbnb"),
    ("liveanywhere",   "LiveAnywhere"),
    ("encostay",       "Encostay"),
    ("zaristay",       "자리톡"),
    ("zigbang",        "직방"),
    ("mister_mention", "미스터멘션"),
    ("33m2",           "삼삼엠투"),
]

# Fixed display order for [정책/공지] — 6 competitors (자리톡 제외)
_POLICY_ORDER = [
    ("airbnb",         "Airbnb"),
    ("liveanywhere",   "리브애니웨어"),
    ("encostay",       "엔코스테이"),
    ("mister_mention", "미스터멘션"),
    ("zigbang",        "직방"),
    ("33m2",           "삼삼엠투"),
]

# Maps (competitor_key, region_key) → (Korean display name, Korean region label)
_SUPPLY_DISPLAY = {
    ("airbnb",       "seoul"):      ("Airbnb",      "서울"),
    ("liveanywhere", "seoul"):      ("리브애니웨어", "서울"),
    ("liveanywhere", "nationwide"): ("리브애니웨어", "국내"),
    ("encostay",     "nationwide"): ("엔코스테이",   "국내"),
}
_SUPPLY_ORDER = ["Airbnb", "리브애니웨어", "엔코스테이"]


def _build_summary(
    date: str,
    meta_ad_stats: dict,
    supply_stats: dict,
    policy_stats: dict,
) -> str:
    lines = [
        f"*경쟁사 모니터링 일일 리포트* ({date})",
        "",
        "*[Meta 광고]*",
    ]

    # Index meta_ad_start_dates results by competitor key.
    # displayed_meta_count = page header count (e.g. "결과 ~80개" → 80)
    # written = rows actually appended to meta_ad_start_dates this run
    meta_by_key = {r.get("competitor"): r for r in meta_ad_stats.get("results", [])}
    for comp_key, display_name in _META_ORDER:
        r = meta_by_key.get(comp_key)
        if r is None or r.get("status") == "failed":
            meta_str = "수집 실패"
        else:
            # Prefer page-header count; fall back to written row count
            count = r.get("displayed_meta_count")
            if count is None:
                count = r.get("written", 0)
            meta_str = f"{count}개"
        lines.append(f"• {display_name}: {meta_str}")

    # ── [공개방 수] ──────────────────────────────────────────────────────────
    lines += ["", "*[공개방 수]*"]

    grouped: dict[str, list[str]] = {}
    for r in supply_stats.get("results", []):
        key = (r.get("competitor", ""), r.get("region", ""))
        mapping = _SUPPLY_DISPLAY.get(key)
        if mapping is None:
            continue
        display_comp, display_region = mapping
        count = r.get("count")
        status = r.get("status", "failed")
        if count is not None:
            entry = f"{display_region} {count:,}개"
        elif status == "login_required":
            entry = f"{display_region} 로그인 필요"
        else:
            entry = f"{display_region} 수집 실패"
        grouped.setdefault(display_comp, []).append(entry)

    for display_comp in _SUPPLY_ORDER:
        entries = grouped.get(display_comp)
        if entries:
            lines.append(f"• {display_comp}: {', '.join(entries)}")

    # ── [정책/공지] ──────────────────────────────────────────────────────────
    lines += ["", "*[정책/공지]*"]

    # Keep only the first result per competitor (one page per competitor)
    policy_by_key: dict[str, dict] = {}
    for r in policy_stats.get("results", []):
        comp = r.get("competitor", "")
        if comp not in policy_by_key:
            policy_by_key[comp] = r

    for comp_key, display_name in _POLICY_ORDER:
        r = policy_by_key.get(comp_key)
        if r is None:
            lines.append(f"• {display_name}: 수집 실패")
            continue
        status = r.get("status", "failed")
        if status == "ok" and r.get("is_new"):
            lines.append(f"• {display_name}: 신규")
        elif status == "ok" and r.get("is_changed"):
            lines.append(f"• {display_name}: 변경됨")
        elif status == "ok":
            lines.append(f"• {display_name}: 변경 없음")
        else:
            lines.append(f"• {display_name}: 수집 실패")

    total_failed = (
        meta_ad_stats.get("failed", 0)
        + supply_stats.get("failed", 0)
        + policy_stats.get("failed", 0)
    )
    finished = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines += [
        "",
        f"실패: {total_failed}건 | 완료: {finished}",
    ]
    return "\n".join(lines)


def run() -> None:
    run_started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = run_started_at[:10]
    print(f"\n=== run_daily start ({run_started_at}) ===\n")

    # ── Workflow 1: Meta 광고 게재일 수집 ────────────────────────────────────
    print(">>> [1/3] Meta 광고 게재일 수집 (meta_ad_start_dates)")
    meta_ad_stats: dict = {"checked": 0, "failed": 0, "written": 0, "results": []}
    try:
        meta_ad_stats = collect_meta_ad_start_dates.run()
    except Exception as exc:
        print(f"[ERROR] collect_meta_ad_start_dates failed: {exc}", file=sys.stderr)
        meta_ad_stats["failed"] += len(_META_ORDER)

    # ── Workflow 2: Supply collection ────────────────────────────────────────
    print("\n>>> [2/3] 방 개수 수집")
    supply_stats: dict = {"checked": 0, "failed": 0, "results": []}
    try:
        supply_stats = collect_supply.run()
    except Exception as exc:
        print(f"[ERROR] collect_supply failed: {exc}", file=sys.stderr)
        supply_stats["failed"] += 3

    # ── Workflow 3: Policy / Notice board updates ────────────────────────────
    print("\n>>> [3/3] 정책/공지 확인")
    policy_stats: dict = {
        "checked": 0, "new_count": 0, "changed_count": 0, "failed": 0, "results": [],
    }
    try:
        policy_stats = detect_policy_changes.run()
    except Exception as exc:
        print(f"[ERROR] detect_policy_changes failed: {exc}", file=sys.stderr)

    run_finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_failed = (
        meta_ad_stats.get("failed", 0)
        + supply_stats.get("failed", 0)
        + policy_stats.get("failed", 0)
    )
    status = "ok" if total_failed == 0 else ("partial" if total_failed < 5 else "failed")

    # ── Build Korean summary ─────────────────────────────────────────────────
    summary_ko = _build_summary(today, meta_ad_stats, supply_stats, policy_stats)

    # ── Write run_log row ────────────────────────────────────────────────────
    ensure_headers("run_log")
    log_row = {
        "run_started_at": run_started_at,
        "run_finished_at": run_finished_at,
        "meta_checked_count": meta_ad_stats.get("checked", 0),
        "google_checked_count": 0,
        "policy_checked_count": policy_stats.get("checked", 0),
        "new_policy_count": policy_stats.get("new_count", 0),
        "changed_policy_count": policy_stats.get("changed_count", 0),
        "failed_count": total_failed,
        "summary_ko": summary_ko[:1000],
        "status": status,
    }
    if append_row("run_log", log_row):
        print("\n[run_log] 기록 완료")
    else:
        print("\n[WARN] run_log 기록 실패", file=sys.stderr)

    # ── Send single Korean Google Chat summary ───────────────────────────────
    print("\n>>> Google Chat 요약 발송…")
    sent = send_google_chat_message(summary_ko)
    if sent:
        print("[OK] Google Chat 요약 발송 완료")
    else:
        print("[WARN] Google Chat 발송 실패", file=sys.stderr)

    print(f"\n=== run_daily 완료 (status={status}) ===")
    sys.exit(0 if status in ("ok", "partial") else 1)


if __name__ == "__main__":
    run()
