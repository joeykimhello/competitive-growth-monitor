"""FastAPI server — exposes job trigger endpoints for n8n Cloud.

n8n Cloud cannot execute local shell commands, so each job is exposed as
an HTTP endpoint that n8n calls via an HTTP Request node on a schedule.

Endpoints:
  GET  /health                    — liveness check (no auth)
  POST /run/collect-ads           — runs collect_ads.run()
  POST /run/detect-policy-changes — runs detect_policy_changes.run()

Auth:
  All /run/* endpoints require the header:
    X-Automation-Token: <value of AUTOMATION_API_TOKEN env var>
  Returns 401 if missing or wrong. Returns 500 if the server env var is unset.

Concurrency:
  Each endpoint holds an asyncio.Lock. If the same job is already running a
  second request returns 409 immediately rather than queueing.

Job output (stdout/stderr) is written to the process log (Cloud Run captures
this automatically). Secrets are never included in HTTP responses.

Run locally:
  uvicorn src.api.server:app --host 0.0.0.0 --port 8080 --reload

Production (Cloud Run):
  uvicorn src.api.server:app --host 0.0.0.0 --port $PORT
"""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

load_dotenv()

from src.jobs import collect_ads, detect_policy_changes  # noqa: E402

_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _locks["collect-ads"] = asyncio.Lock()
    _locks["detect-policy-changes"] = asyncio.Lock()
    yield


app = FastAPI(
    title="competitive-growth-monitor",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


class JobResult(BaseModel):
    status: str      # "ok" | "error"
    job_name: str
    started_at: str  # ISO 8601 UTC
    finished_at: str
    success: bool
    message: str


def _check_token(x_automation_token: Optional[str]) -> None:
    expected = os.environ.get("AUTOMATION_API_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="Server misconfiguration: AUTOMATION_API_TOKEN is not set.",
        )
    if not x_automation_token or x_automation_token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Automation-Token.")


async def _run_job(name: str, fn) -> JobResult:
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        # Run the synchronous job in a thread so it does not block the event loop.
        # Jobs that call sys.exit() raise SystemExit, which run_in_executor
        # stores in the Future and re-raises here.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, fn)
        return JobResult(
            status="ok",
            job_name=name,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            success=True,
            message="Job completed successfully.",
        )
    except SystemExit as exc:
        return JobResult(
            status="error",
            job_name=name,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            success=False,
            message=f"Job exited with code {exc.code}.",
        )
    except Exception as exc:
        return JobResult(
            status="error",
            job_name=name,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            success=False,
            message=f"{type(exc).__name__}: {exc}",
        )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/run/collect-ads", response_model=JobResult)
async def run_collect_ads(x_automation_token: Optional[str] = Header(None)):
    _check_token(x_automation_token)
    lock = _locks["collect-ads"]
    if lock.locked():
        raise HTTPException(status_code=409, detail="collect-ads is already running.")
    async with lock:
        return await _run_job("collect-ads", collect_ads.run)


@app.post("/run/detect-policy-changes", response_model=JobResult)
async def run_detect_policy_changes(x_automation_token: Optional[str] = Header(None)):
    _check_token(x_automation_token)
    lock = _locks["detect-policy-changes"]
    if lock.locked():
        raise HTTPException(status_code=409, detail="detect-policy-changes is already running.")
    async with lock:
        return await _run_job("detect-policy-changes", detect_policy_changes.run)
