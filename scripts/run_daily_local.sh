#!/usr/bin/env bash
# Daily local run — runs all workflows via run_daily.py
#
# Usage:
#   bash scripts/run_daily_local.sh
#
# Runs: 광고 수집 → 방 개수 수집 → 정책/공지 확인
# 완료 후 Google Chat으로 한국어 요약 1건 발송.
#
# To log output:
#   bash scripts/run_daily_local.sh >> ~/cgm-daily.log 2>&1

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ ! -f ".venv/bin/activate" ]]; then
    echo "[ERROR] .venv not found. Run: python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
  fi
  source .venv/bin/activate
fi

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

log "=== run_daily_local start ==="
python -m src.jobs.run_daily
RC=$?
log "=== run_daily 완료 (exit=$RC) ==="

log "=== run_daily_local done (exit=$RC) ==="
exit $RC
