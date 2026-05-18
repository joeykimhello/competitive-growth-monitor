# n8n Workflow Guide — competitive-growth-monitor

> **현재 운영 방식:** n8n Cloud는 현재 사용하지 않습니다.
> MVP는 로컬 Mac에서 `bash scripts/run_daily_local.sh`로 실행합니다.
>
> 이 문서는 향후 n8n Cloud + Cloud Run 도입 시를 위한 **참고 자료**입니다.
> 배포 가이드: [`docs/deployment.md`](deployment.md)

---

When n8n Cloud is introduced, it will orchestrate scheduled job execution by
calling the FastAPI server deployed on Google Cloud Run via HTTP Request nodes.
Execute Command is not available on n8n Cloud, so direct shell invocation is
only suitable for local development.

---

## Current State — 로컬 Mac 직접 실행

```bash
# 기본 실행 (두 MVP Job 순차 실행)
bash scripts/run_daily_local.sh

# 개별 실행 (디버깅)
source .venv/bin/activate
python -m src.jobs.collect_ads
python -m src.jobs.detect_policy_changes

# Supply collection — 수동 PoC용, 정기 실행 제외
# python -m src.jobs.collect_supply
```

macOS 자동 실행 옵션(cron/launchd): README.md 참고.

---

## Future: n8n + Cloud Run Scope (Reference)

When n8n Cloud is introduced, the first two scheduled workflows would be:

| # | Workflow | Schedule | Endpoint | Sheets Tab |
|---|----------|----------|----------|------------|
| 1 | `collect-ads` | Daily 07:00 KST | `POST /run/collect-ads` | `ad_activity_snapshots` |
| 2 | `detect-policy-changes` | Every 6 hours | `POST /run/detect-policy-changes` | `policy_change_log` |

**Excluded from MVP (deferred):**

| Workflow | Reason |
|----------|--------|
| `collect-supply` | Encostay is map-based (unreliable count); LiveAnywhere regional filters need more automation work; Airbnb supply comes from AirDNA which requires an authorized login session. Code and `raw_supply_snapshots` tab are retained for a later PoC. |
| `collect-reputation` | Not yet implemented. |

The Looker Studio dashboard is initially built on `ad_activity_snapshots` and `policy_change_log`.  
Google Chat alerts cover: policy changes, ad collection failures, and (later) ad activity spikes.

---

## Architecture

```
[n8n Cloud]
  Schedule Trigger (cron)
      ▼
  HTTP Request node
      │  POST https://your-api.run.app/run/<job>
      │  Header: X-Automation-Token: {{ $env.AUTOMATION_API_TOKEN }}
      ▼
[FastAPI on Cloud Run]  ──►  [Google Sheets]  ──►  [Looker Studio]
                          ──►  [Google Chat Alerts]
```

The API server URL is the Cloud Run service URL set in n8n's environment or
credential store — never hardcoded in the workflow.

---

## Prerequisites

- n8n Cloud account with an active workflow
- FastAPI server deployed on Google Cloud Run (see [`docs/deployment.md`](deployment.md))
- `AUTOMATION_API_TOKEN` stored as an n8n environment variable or credential
- API server `AUTOMATION_API_TOKEN` env var set to the same value

---

## MVP Workflow 1 — `collect-ads`

**Purpose:** Daily ad snapshot from Meta Ad Library and Google Ads Transparency Center  
**Schedule:** Every day at 07:00 KST (22:00 UTC)

### n8n nodes

```
[Schedule Trigger]
  Cron: 0 22 * * *
    ▼
[HTTP Request]
  Method:  POST
  URL:     https://your-api.run.app/run/collect-ads
  Headers: X-Automation-Token = {{ $env.AUTOMATION_API_TOKEN }}
  Timeout: 300s  (ad collection uses Playwright — allow up to 5 min)
    ▼
[IF]
  Condition: {{ $json.success }} == false
    ├─ true  → [Google Chat] "collect-ads failed: {{ $json.message }}"
    └─ false → [No-op]
```

**Expected response (success):**
```json
{
  "status": "ok",
  "job_name": "collect-ads",
  "started_at": "2026-05-08T22:00:01Z",
  "finished_at": "2026-05-08T22:03:47Z",
  "success": true,
  "message": "Job completed successfully."
}
```

**Writes to:** `ad_activity_snapshots`  
**Alerts:** Google Chat on `success=false` (via n8n IF node).

---

## MVP Workflow 2 — `detect-policy-changes`

**Purpose:** Detect competitor policy page changes; alert on change and on failure  
**Schedule:** Every 6 hours (00:00, 06:00, 12:00, 18:00 UTC)

### n8n nodes

```
[Schedule Trigger]
  Cron: 0 */6 * * *
    ▼
[HTTP Request]
  Method:  POST
  URL:     https://your-api.run.app/run/detect-policy-changes
  Headers: X-Automation-Token = {{ $env.AUTOMATION_API_TOKEN }}
  Timeout: 120s
    ▼
[IF]
  Condition: {{ $json.success }} == false
    ├─ true  → [Google Chat] "detect-policy-changes failed: {{ $json.message }}"
    └─ false → [No-op]
    (Policy change alerts are sent inside the job itself, not by n8n)
```

**Writes to:** `policy_change_log`  
**Alerts:** Google Chat on detected change (inside job) and on `success=false` (via n8n).

---

## HTTP Response Reference

All `/run/*` endpoints return the same JSON shape:

| Field | Type | Notes |
|-------|------|-------|
| `status` | string | `"ok"` or `"error"` |
| `job_name` | string | e.g. `"collect-ads"` |
| `started_at` | string | ISO 8601 UTC |
| `finished_at` | string | ISO 8601 UTC |
| `success` | boolean | `false` if job exited non-zero or threw an exception |
| `message` | string | Human-readable outcome; no secrets included |

**HTTP status codes:**

| Code | Meaning |
|------|---------|
| 200 | Request accepted; check `success` field for job outcome |
| 401 | Missing or invalid `X-Automation-Token` |
| 409 | Job is already running (concurrent trigger) |
| 500 | Server misconfiguration (e.g. `AUTOMATION_API_TOKEN` not set) |

---

## Environment Variable in n8n

Store the API token as an n8n environment variable named `AUTOMATION_API_TOKEN`
(Settings → Variables). Reference it in HTTP Request headers as:

```
X-Automation-Token: {{ $env.AUTOMATION_API_TOKEN }}
```

Do not paste the token value directly into the workflow — use the variable reference.

---

## Error Handling

- **Job-level errors:** caught inside `_run_job`; returned as `success=false` in JSON body with HTTP 200. The n8n IF node checks `$json.success`.
- **Auth errors:** HTTP 401 — n8n will treat this as a failed node execution.
- **Concurrent conflict:** HTTP 409 — n8n retries if retry is configured; otherwise mark as failed.
- **Recommended n8n retry:** 2 attempts, 5-minute interval, for transient network errors.
- **Timeout:** Set node timeout to at least 300s for `collect-ads` (Playwright is slow).

---

## Deferred — `collect-supply`

Not scheduled in MVP. Code lives in `src/collectors/supply/` and `src/jobs/collect_supply.py`.  
The `raw_supply_snapshots` tab is pre-created and ready to receive data when revisited.

**Reasons for deferral:**
- **Encostay** renders listings on a map; a reliable total count is not in a stable DOM element.
- **LiveAnywhere** regional filters ("서울 전체", "국내 전체") require additional automation work.
- **Airbnb** supply count comes from AirDNA (`app.airdna.co`), which requires an authorized login session — not suitable for unattended scheduled execution without a session management solution.

**Future endpoint (not yet implemented):** `POST /run/collect-supply`

---

## Future Workflow — `collect-reputation`

Not yet implemented. Planned for a later phase after MVP stabilizes.  
**Future endpoint (not yet implemented):** `POST /run/collect-reputation`
