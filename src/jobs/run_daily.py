"""Daily run orchestrator.

Usage:
    python -m src.jobs.run_daily

Runs all four workflows in sequence:
  1. Ad collection — Meta Ad Library + Google Ads Transparency (collect_ads)
  2. Supply collection — listing/house counts (collect_supply)
  3. Policy / Notice board updates (detect_policy_changes)
  4. App version monitoring — iOS App Store + Android Google Play (collect_app_versions)

After all workflows finish:
  - Writes one row to the run_log sheet tab
  - Sends a single Korean summary to Google Chat

All Google Chat messages are in Korean. No per-page English alerts are sent.
"""

import sys
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from src.jobs import collect_meta_ad_start_dates, collect_supply, detect_policy_changes, collect_app_versions
from src.integrations.google_sheets import append_row, ensure_headers, read_sheet_rows
from src.integrations.google_chat import send_google_chat_message

load_dotenv()

# Fixed display order for [Meta 광고] — 8 competitors
_META_ORDER = [
    ("airbnb",         "Airbnb"),
    ("liveanywhere",   "LiveAnywhere"),
    ("encostay",       "Encostay"),
    ("zaristay",       "자리톡"),
    ("zigbang",        "직방"),
    ("mister_mention", "미스터멘션"),
    ("33m2_1",         "삼삼엠투1"),
    ("33m2_2",         "삼삼엠투2"),
]

# Fixed display order for [앱 업데이트] — 7 competitors (33m2 is single key in app_sources)
_APP_ORDER = [
    ("airbnb",         "Airbnb"),
    ("liveanywhere",   "리브애니웨어"),
    ("encostay",       "엔코스테이"),
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
    ("33m2",         "nationwide"): ("삼삼엠투",     "국내"),
    ("33m2",         "seoul"):      ("삼삼엠투",     "서울"),
}
_SUPPLY_ORDER = ["Airbnb", "리브애니웨어", "엔코스테이", "삼삼엠투"]


def _get_previous_meta_counts(today: str) -> dict:
    """Return {competitor_key: {"date": str, "count": int}} for the most recent date < today.

    Only non-failed rows are considered. Reads the meta_ad_counts sheet tab.
    Returns {} if the tab is empty, unreadable, or no qualifying rows exist.
    """
    rows = read_sheet_rows("meta_ad_counts")
    by_comp: dict = {}
    for row in rows:
        d = row.get("date", "")
        comp = row.get("competitor", "")
        cnt_str = row.get("displayed_meta_count", "")
        status = row.get("status", "")
        # Exclude: today's rows, failed rows, rows with no count
        if not d or not comp or not cnt_str or d >= today:
            continue
        if status == "failed":
            continue
        try:
            by_comp.setdefault(comp, []).append((d, int(cnt_str)))
        except (ValueError, TypeError):
            continue
    # For each competitor pick the count from the most recent qualifying date
    result = {}
    for comp, entries in by_comp.items():
        entries.sort(key=lambda x: x[0], reverse=True)
        best_date, best_count = entries[0]
        result[comp] = {"date": best_date, "count": best_count}
    return result


def _build_summary(
    date: str,
    meta_ad_stats: dict,
    supply_stats: dict,
    policy_stats: dict,
    prev_meta_counts: dict,
    app_stats: Optional[dict] = None,
) -> str:
    lines = [
        f"*경쟁사 모니터링 일일 리포트* ({date})",
        "",
        "*[Meta 광고]*",
    ]

    # ── [Meta 광고] — always shown ───────────────────────────────────────────
    # displayed_meta_count = page header count (e.g. "결과 ~80개" → 80)
    meta_by_key = {r.get("competitor"): r for r in meta_ad_stats.get("results", [])}
    for comp_key, display_name in _META_ORDER:
        r = meta_by_key.get(comp_key)
        if r is None or r.get("status") == "failed":
            meta_str = "수집 실패"
            print(
                f"[META_SUMMARY_DEBUG] competitor={comp_key} display_name={display_name}"
                f" today_date={date} today_count=None"
                f" previous_date=N/A previous_count=N/A delta=N/A delta_label=N/A"
            )
        else:
            count = r.get("displayed_meta_count")
            prev_info = prev_meta_counts.get(comp_key)
            prev = prev_info["count"] if prev_info else None
            prev_date = prev_info["date"] if prev_info else None

            if count == 0:
                delta = (count - prev) if prev is not None else None
                delta_str = f"(-{prev}개)" if prev else ""
                meta_str = f"광고 없음{delta_str}"
            elif count is not None and prev is not None:
                delta = count - prev
                delta_str = f"(+{delta}개)" if delta > 0 else (f"({delta}개)" if delta < 0 else "(0개)")
                meta_str = f"{count}개{delta_str}"
            elif count is not None:
                delta = None
                delta_str = "(신규 기준)"
                meta_str = f"{count}개{delta_str}"
            else:
                delta = None
                delta_str = ""
                meta_str = "수집 실패"

            long_count = r.get("long_running_count", 0)
            print(
                f"[META_SUMMARY_DEBUG] competitor={comp_key} display_name={display_name}"
                f" today_date={date} today_count={count}"
                f" previous_date={prev_date} previous_count={prev}"
                f" delta={delta} delta_label={delta_str}"
                f" long_running_30d_exceeded_count={long_count}"
            )
        lines.append(f"• {display_name}: {meta_str}")

    # ── [Meta 30일 초과 광고] — always shown ─────────────────────────────────
    lines += ["", "*[Meta 30일 초과 광고]*"]
    for comp_key, display_name in _META_ORDER:
        r = meta_by_key.get(comp_key)
        if r is None or r.get("status") == "failed":
            lines.append(f"• {display_name}: 수집 실패")
        else:
            long_count = r.get("long_running_count", 0)
            lines.append(f"• {display_name}: {long_count}개")

    # ── [공개방 수] — always shown ───────────────────────────────────────────
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
        if count is not None and status == "sheet_write_failed":
            entry = f"{display_region} {count:,}개 (시트 저장 실패)"
        elif count is not None:
            entry = f"{display_region} {count:,}개"
        elif status == "login_required":
            entry = f"{display_region} 로그인 필요"
        elif status == "sheet_write_failed":
            entry = f"{display_region} 시트 저장 실패"
        else:
            entry = f"{display_region} 수집 실패"
        grouped.setdefault(display_comp, []).append(entry)

    for display_comp in _SUPPLY_ORDER:
        entries = grouped.get(display_comp)
        if entries:
            lines.append(f"• {display_comp}: {', '.join(entries)}")

    # ── [시트 기록 실패] — only shown when meta_ad_counts write failed ─────────
    meta_count_write_failed = [
        display_name
        for comp_key, display_name in _META_ORDER
        if not meta_by_key.get(comp_key, {}).get("meta_count_sheet_ok", True)
        and meta_by_key.get(comp_key) is not None
    ]
    if meta_count_write_failed:
        lines += ["", "*[시트 기록 실패 - Meta 광고 수]*"]
        for name in meta_count_write_failed:
            lines.append(f"• {name}: meta_ad_counts 저장 실패 (다음 실행 시 전일 비교 불가)")

    # ── [정책/공지] — only shown when is_new or is_changed exists ────────────
    # Collect all changed pages grouped by competitor key
    policy_changed: dict[str, list[dict]] = {}
    for r in policy_stats.get("results", []):
        if r.get("is_new") or r.get("is_changed"):
            comp = r.get("competitor", "")
            policy_changed.setdefault(comp, []).append(r)

    if policy_changed:
        lines += ["", "*[정책/공지]*"]
        for comp_key, display_name in _POLICY_ORDER:
            changed_pages = policy_changed.get(comp_key)
            if not changed_pages:
                continue
            for r in changed_pages:
                change_type = "새 공지" if r.get("is_new") else "변경됨"
                title = r.get("title", "")
                lines.append(f"• {display_name}: {change_type}")
                if title:
                    lines.append(f"  - {title}")

    # ── [앱 업데이트] — only shown when is_changed=True exists ───────────────
    app_changed_set: set[tuple] = set()
    if app_stats:
        for r in app_stats.get("results", []):
            if r.get("is_changed"):
                app_changed_set.add((r.get("competitor"), r.get("platform")))

    if app_changed_set:
        lines += ["", "*[앱 업데이트]*"]
        app_by_key: dict = {}
        for r in (app_stats or {}).get("results", []):
            app_by_key[(r.get("competitor"), r.get("platform"))] = r

        for comp_key, display_name in _APP_ORDER:
            for platform in ("ios", "android"):
                if (comp_key, platform) not in app_changed_set:
                    continue
                r = app_by_key.get((comp_key, platform))
                if r is None:
                    continue
                plat_label = "iOS" if platform == "ios" else "Android"
                ver = r.get("version", "")
                ver_str = ver if ver else "버전 없음"
                change_ko = r.get("change_summary_ko", "")
                lines.append(f"• {display_name} {plat_label}: {ver_str}")
                if change_ko:
                    lines.append(f"  - {change_ko}")

    # ── [앱 설명 변경] — only shown when is_description_changed=True exists ─────
    desc_changed_set: set[tuple] = set()
    if app_stats:
        for r in app_stats.get("results", []):
            if r.get("is_description_changed"):
                desc_changed_set.add((r.get("competitor"), r.get("platform")))

    if desc_changed_set:
        lines += ["", "*[앱 설명 변경]*"]
        app_by_key_desc: dict = {}
        for r in (app_stats or {}).get("results", []):
            app_by_key_desc[(r.get("competitor"), r.get("platform"))] = r
        for comp_key, display_name in _APP_ORDER:
            for platform in ("ios", "android"):
                if (comp_key, platform) not in desc_changed_set:
                    continue
                r = app_by_key_desc.get((comp_key, platform))
                if r is None:
                    continue
                plat_label = "iOS" if platform == "ios" else "Android"
                lines.append(f"• {display_name} {plat_label}: 설명 변경")

    total_failed = (
        meta_ad_stats.get("failed", 0)
        + supply_stats.get("failed", 0)
        + policy_stats.get("failed", 0)
        + (app_stats.get("failed", 0) if app_stats else 0)
    )

    # ── [실패 상세] — only shown when there are counted failures ─────────────
    _POLICY_STATUS_KO = {
        "fetch_failed": "페이지 수집 실패",
        "timeout": "시간 초과",
        "no_post": "게시물 파싱 실패",
        "failed": "수집 실패",
    }
    _META_DISPLAY = dict(_META_ORDER)
    failure_details: list[str] = []

    for r in policy_stats.get("results", []):
        status = r.get("status", "")
        if status in _POLICY_STATUS_KO:
            display = r.get("competitor_display", r.get("competitor", ""))
            failure_details.append(f"정책({display}): {_POLICY_STATUS_KO[status]}")

    for r in meta_ad_stats.get("results", []):
        if r.get("status") == "failed":
            display = _META_DISPLAY.get(r.get("competitor", ""), r.get("competitor", ""))
            failure_details.append(f"Meta 광고({display}): 수집 실패")

    for comp_key, display_name in _META_ORDER:
        if comp_key not in {r.get("competitor") for r in meta_ad_stats.get("results", [])}:
            if meta_ad_stats.get("failed", 0) > 0:
                failure_details.append(f"Meta 광고({display_name}): 수집 실패")

    for r in supply_stats.get("results", []):
        status = r.get("status", "")
        if status not in ("ok", "sheet_write_failed", ""):
            key = (r.get("competitor", ""), r.get("region", ""))
            mapping = _SUPPLY_DISPLAY.get(key)
            label = f"{mapping[0]} {mapping[1]}" if mapping else f"{r.get('competitor')}/{r.get('region')}"
            failure_details.append(f"공개방 수({label}): {status}")

    for r in (app_stats or {}).get("results", []):
        if r.get("status") == "failed":
            comp = r.get("competitor", "")
            platform = r.get("platform", "")
            failure_details.append(f"앱({comp}/{platform}): 수집 실패")

    if failure_details:
        lines += ["", "*[실패 상세]*"]
        for detail in failure_details:
            lines.append(f"• {detail}")

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
    print(">>> [1/4] Meta 광고 게재일 수집 (meta_ad_start_dates)")
    meta_ad_stats: dict = {"checked": 0, "failed": 0, "written": 0, "results": []}
    try:
        meta_ad_stats = collect_meta_ad_start_dates.run()
    except Exception as exc:
        print(f"[ERROR] collect_meta_ad_start_dates failed: {exc}", file=sys.stderr)
        meta_ad_stats["failed"] += len(_META_ORDER)

    # ── Workflow 2: Supply collection ────────────────────────────────────────
    print("\n>>> [2/4] 방 개수 수집")
    supply_stats: dict = {"checked": 0, "failed": 0, "results": []}
    try:
        supply_stats = collect_supply.run()
    except Exception as exc:
        print(f"[ERROR] collect_supply failed: {exc}", file=sys.stderr)
        supply_stats["failed"] += 3

    # ── Workflow 3: Policy / Notice board updates ────────────────────────────
    print("\n>>> [3/4] 정책/공지 확인")
    policy_stats: dict = {
        "checked": 0, "new_count": 0, "changed_count": 0, "failed": 0, "results": [],
    }
    try:
        policy_stats = detect_policy_changes.run()
    except Exception as exc:
        print(f"[ERROR] detect_policy_changes failed: {exc}", file=sys.stderr)

    # ── Workflow 4: App version monitoring ───────────────────────────────────
    print("\n>>> [4/4] 앱 버전 수집")
    app_stats: dict = {"checked": 0, "failed": 0, "results": []}
    try:
        app_stats = collect_app_versions.run()
    except Exception as exc:
        print(f"[ERROR] collect_app_versions failed: {exc}", file=sys.stderr)

    run_finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_failed = (
        meta_ad_stats.get("failed", 0)
        + supply_stats.get("failed", 0)
        + policy_stats.get("failed", 0)
        + app_stats.get("failed", 0)
    )
    status = "ok" if total_failed == 0 else ("partial" if total_failed < 5 else "failed")

    # ── Build Korean summary ─────────────────────────────────────────────────
    prev_meta_counts = _get_previous_meta_counts(today)
    summary_ko = _build_summary(today, meta_ad_stats, supply_stats, policy_stats, prev_meta_counts, app_stats)

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
