# GitHub Actions 배포 가이드

## 개요

`.github/workflows/daily-monitoring.yml`이 매일 09:00 KST에 자동으로 실행됩니다.  
`run_daily.py` 전체 워크플로우(Meta 광고 → 방 개수 → 정책/공지)를 실행하고 Google Chat으로 요약을 발송합니다.

---

## 스케줄 & 수동 실행

| 방식 | 내용 |
|------|------|
| 자동 | 매일 00:00 UTC = 09:00 KST |
| 수동 | GitHub → Actions → "Daily Monitoring" → "Run workflow" |

---

## GitHub Secrets 등록

리포지터리 Settings → Secrets and variables → Actions → **New repository secret** 에서 아래 4개를 등록합니다.

| Secret 이름 | 값 | 비고 |
|---|---|---|
| `GOOGLE_SHEET_ID` | Google Sheets URL의 `/d/` 뒤 문자열 | `.env`의 `GOOGLE_SHEET_ID` 값과 동일 |
| `GOOGLE_CHAT_WEBHOOK_URL` | Google Chat 수신 Webhook URL | `.env`의 `GOOGLE_CHAT_WEBHOOK_URL` 값과 동일 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 서비스 계정 JSON 파일 전체 내용 | `cat` 명령어로 복사 (아래 참조) |
| `AIRDNA_STORAGE_STATE_JSON` | AirDNA 로그인 세션 JSON 파일 전체 내용 | `.auth/airdna_state.json` (아래 참조) |

### 서비스 계정 JSON 복사 방법

```bash
# 로컬에서 실행 — 출력 내용 전체를 Secret 값으로 붙여넣기
cat .secrets/google_service_account.json
```

### AirDNA 세션 JSON 복사 방법

```bash
cat .auth/airdna_state.json
```

AirDNA는 로그인이 필요한 대시보드입니다. 세션이 만료되면 수집이 실패합니다.

---

## AirDNA 세션 갱신

AirDNA 세션(`airdna_state.json`)은 만료되면 수집 실패(`login_required`)가 발생합니다.  
만료 시 아래 절차로 갱신합니다.

```bash
# 로컬에서 실행
source .venv/bin/activate
python scripts/setup_airdna_session.py
```

스크립트가 브라우저를 열면 AirDNA에 수동 로그인 후 엔터를 누릅니다.  
갱신된 `.auth/airdna_state.json`을 `AIRDNA_STORAGE_STATE_JSON` Secret에 다시 등록합니다.

---

## 실패 시 확인 순서

1. **GitHub Actions 로그 확인**  
   Actions 탭 → 실패한 실행 → 각 step 클릭 → 에러 메시지 확인

2. **공통 실패 원인**

   | 증상 | 원인 | 조치 |
   |---|---|---|
   | `GOOGLE_APPLICATION_CREDENTIALS` 관련 에러 | 서비스 계정 Secret 누락/오기입 | Secret 재등록 |
   | AirDNA `login_required` | 세션 만료 | AirDNA 세션 갱신 후 Secret 업데이트 |
   | `ModuleNotFoundError` | `requirements.txt` 누락 패키지 | 로컬 `pip freeze`로 확인 후 추가 |
   | Playwright timeout | 사이트 응답 지연 | 재실행(workflow_dispatch)으로 재시도 |
   | Google Sheets API 403 | 서비스 계정 권한 없음 | 스프레드시트에 서비스 계정 이메일 공유 확인 |

3. **Google Chat 요약 미수신**  
   Actions 로그에서 `[OK] Google Chat 요약 발송 완료` 또는 `[WARN] Google Chat 발송 실패` 확인.  
   실패 시 `GOOGLE_CHAT_WEBHOOK_URL` Secret 값 확인.

---

## 워크플로우 구조

```
checkout
  → Python 3.11 + pip cache
  → pip install -r requirements.txt
  → playwright install chromium --with-deps
  → mkdir (.auth, .secrets, data/snapshots/*)
  → echo GOOGLE_SERVICE_ACCOUNT_JSON > .secrets/google_service_account.json
  → echo AIRDNA_STORAGE_STATE_JSON    > .auth/airdna_state.json
  → python -m src.jobs.run_daily
       ├─ collect_meta_ad_start_dates  (Meta 광고)
       ├─ collect_supply               (방 개수)
       ├─ detect_policy_changes        (정책/공지)
       └─ send_google_chat_message     (Korean 요약 발송)
```
