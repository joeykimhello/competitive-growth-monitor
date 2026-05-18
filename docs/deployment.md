# Deployment Guide — competitive-growth-monitor API

> **현재 운영 방식:** 이 프로젝트는 현재 **로컬 Mac에서만** 실행됩니다.
> `bash scripts/run_daily_local.sh`가 기본 실행 방법입니다.
>
> 이 문서는 향후 팀 공유 환경으로 전환할 때를 위한 **참고 자료**입니다.
> 현재 단계에서는 Cloud Run, n8n Cloud, GitHub Actions를 사용하지 않습니다.

---

The FastAPI server (`src/api/server.py`) can be deployed to Google Cloud Run
so that n8n Cloud can call it via HTTP Request nodes on a schedule.

**Target: Google Cloud Run** (when shared deployment is needed)  
Serverless, scales to zero when idle, and integrates natively with Google
Secret Manager for credential management.

---

## Required Environment Variables

Set on the Cloud Run service — never committed to the repo.

| Variable | Required | Description |
|----------|----------|-------------|
| `AUTOMATION_API_TOKEN` | **Yes** | Secret token for `X-Automation-Token` header. Generate with `openssl rand -hex 32`. |
| `GOOGLE_SHEET_ID` | **Yes** | Google Sheets spreadsheet ID (from the URL). |
| `GOOGLE_APPLICATION_CREDENTIALS` | **Yes** | Path to the service account JSON file mounted inside the container, e.g. `/secrets/sa.json`. |
| `GOOGLE_CHAT_WEBHOOK_URL` | **Yes** | Google Chat Incoming Webhook URL for alerts. |
| `ENV` | No | `prod` (default: `dev`). Written to each Sheets row. |
| `PORT` | No | Injected automatically by Cloud Run. Defaults to `8080` locally. |
| `AIRDNA_USER_DATA_DIR` | No | Only needed for supply collection (deferred from MVP). |

> **Security rule:** Never put credential values, tokens, or service account JSON into the Dockerfile, source code, or any committed file. Use Secret Manager.

---

## Step 1 — Build and Push the Container Image

A `Dockerfile` is included in the repository root. It uses the official
Playwright Python base image (`mcr.microsoft.com/playwright/python:v1.44.0-jammy`)
which bundles Chromium and all system dependencies.

```bash
export PROJECT_ID=your-gcp-project-id
export REGION=asia-northeast3        # Seoul — adjust as needed
export IMAGE=gcr.io/$PROJECT_ID/competitive-growth-monitor

# Authenticate Docker to Google Container Registry
gcloud auth configure-docker

# Build and push
docker build -t $IMAGE .
docker push $IMAGE
```

To verify the image starts correctly before pushing to Cloud Run:

```bash
docker run --rm -p 8080:8080 \
  --env-file .env \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/sa.json \
  -v $(pwd)/your-sa-file.json:/app/sa.json:ro \
  $IMAGE

curl http://localhost:8080/health
```

---

## Step 2 — Store Secrets in Google Secret Manager

```bash
# Service account JSON
gcloud secrets create google-sa-json \
  --data-file=./your-service-account.json

# API token (generate first)
openssl rand -hex 32 | tr -d '\n' | \
  gcloud secrets create automation-api-token --data-file=-

# Google Chat webhook URL
echo -n "https://chat.googleapis.com/v1/spaces/.../messages?key=..." | \
  gcloud secrets create google-chat-webhook --data-file=-

# Google Sheet ID
echo -n "your_spreadsheet_id" | \
  gcloud secrets create google-sheet-id --data-file=-
```

Grant the Cloud Run service account access to each secret:

```bash
export SA_EMAIL=your-cloud-run-sa@$PROJECT_ID.iam.gserviceaccount.com

for SECRET in google-sa-json automation-api-token google-chat-webhook google-sheet-id; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/secretmanager.secretAccessor"
done
```

---

## Step 3 — Deploy to Cloud Run

```bash
gcloud run deploy competitive-growth-monitor \
  --image $IMAGE \
  --region $REGION \
  --platform managed \
  --no-allow-unauthenticated \
  --set-env-vars ENV=prod \
  --set-secrets \
    AUTOMATION_API_TOKEN=automation-api-token:latest,\
    GOOGLE_CHAT_WEBHOOK_URL=google-chat-webhook:latest,\
    GOOGLE_SHEET_ID=google-sheet-id:latest \
  --set-secrets /secrets/sa.json=google-sa-json:latest \
  --set-env-vars GOOGLE_APPLICATION_CREDENTIALS=/secrets/sa.json \
  --timeout 300 \
  --memory 2Gi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 2
```

**Why `--min-instances 1`:** The `detect_policy_changes` job writes policy
snapshots to the container's local filesystem for diff comparison between runs.
If the instance is allowed to scale to zero, it is replaced on the next
invocation and the snapshots are lost — every run becomes a "first run" with no
diff detection. Keeping one instance warm preserves the local snapshot cache.

> **Future improvement:** Move policy snapshots to Google Cloud Storage so the
> service can scale to zero without losing state.

---

## Step 4 — Allow n8n Cloud to Call the Service

By default `--no-allow-unauthenticated` requires a Google identity token for
IAM-level access. n8n Cloud cannot obtain one. The simplest approach for an
internal MVP is to open IAM-level access and rely on the `X-Automation-Token`
application header as the security boundary:

```bash
gcloud run services add-iam-policy-binding competitive-growth-monitor \
  --region $REGION \
  --member="allUsers" \
  --role="roles/run.invoker"
```

The `X-Automation-Token` check in the application returns 401 on mismatch.
Do not share or expose the token value.

---

## Step 5 — Verify the Deployment

```bash
export SERVICE_URL=$(gcloud run services describe competitive-growth-monitor \
  --region $REGION --format='value(status.url)')

# Liveness check (no auth required)
curl $SERVICE_URL/health

# Trigger policy change detection
curl -X POST $SERVICE_URL/run/detect-policy-changes \
  -H "X-Automation-Token: $AUTOMATION_API_TOKEN"

# Trigger ad collection (takes up to ~5 min — Playwright)
curl -X POST $SERVICE_URL/run/collect-ads \
  -H "X-Automation-Token: $AUTOMATION_API_TOKEN"
```

Expected success response:

```json
{
  "status": "ok",
  "job_name": "detect-policy-changes",
  "started_at": "2026-05-08T10:00:01.123Z",
  "finished_at": "2026-05-08T10:00:18.456Z",
  "success": true,
  "message": "Job completed successfully."
}
```

---

## Step 6 — Configure n8n Cloud

1. In n8n Cloud → **Settings → Variables**, add:
   - `AUTOMATION_API_TOKEN` — same value as the Cloud Run secret
   - `API_BASE_URL` — the Cloud Run service URL (from `gcloud run services describe`)
2. In each HTTP Request node, set:
   - **URL:** `{{ $env.API_BASE_URL }}/run/collect-ads`
   - **Header:** `X-Automation-Token: {{ $env.AUTOMATION_API_TOKEN }}`
   - **Timeout:** 300s for `collect-ads`, 120s for `detect-policy-changes`
3. See [`docs/n8n_workflows.md`](n8n_workflows.md) for the full workflow structure.

---

## Local Development

```bash
source .venv/bin/activate

# Start the API server locally (reads from .env)
uvicorn src.api.server:app --host 0.0.0.0 --port 8080 --reload

# Test endpoints
curl http://localhost:8080/health

curl -X POST http://localhost:8080/run/detect-policy-changes \
  -H "X-Automation-Token: $AUTOMATION_API_TOKEN"
```

---

## Security Checklist

- [ ] `AUTOMATION_API_TOKEN` is at least 32 random hex characters (`openssl rand -hex 32`)
- [ ] Service account JSON is stored in Secret Manager only — not committed to the repo
- [ ] `.env` and `*.json` files are listed in both `.gitignore` and `.dockerignore`
- [ ] Cloud Run service URL is not shared publicly
- [ ] n8n workflow uses `$env.AUTOMATION_API_TOKEN` variable reference — not an inline value
- [ ] `--min-instances 1` is set to preserve policy snapshot state between runs
