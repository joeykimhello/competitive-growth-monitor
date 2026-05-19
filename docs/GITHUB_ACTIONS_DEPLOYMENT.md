# GitHub Actions 배포 가이드

## 개요

`.github/workflows/daily-monitoring.yml`이 매일 09:00 KST에 자동으로 실행됩니다.  
`run_daily.py` 전체 워크플로우(Meta 광고 → 방 개수 → 정책/공지)를 실행하고 Google Chat으로 요약을 발송합니다.

---

## 스케줄 & 수동 실행

| 방식 | cron (UTC) | KST | 비고 |
|------|-----------|-----|------|
| 자동 (정기) | `0 0 * * *` | 09:00 | 매일 정기 실행 |
| 자동 (임시 테스트) | `48 6 * * *` | 15:48 | 확인 후 제거 예정 |
| 수동 | — | — | GitHub → Actions → "Daily Monitoring" → "Run workflow" |

---

## Schedule 트리거 검증 및 문제 해결

### 전제 조건 체크리스트

자동 schedule이 실행되려면 아래 조건이 모두 충족돼야 합니다.

**1. 워크플로우 파일이 default branch에 있어야 한다**  
`main` 브랜치에만 schedule이 인식됩니다. feature 브랜치에서 push해도 schedule은 동작하지 않습니다.

```bash
# 확인
git branch -r     # origin/main이 default branch인지 확인
git log --oneline origin/main -3  # 최근 커밋에 워크플로우 변경이 포함됐는지 확인
```

**2. `on:` 키가 GitHub Actions에서 인식되는 형태인지**  
YAML에서 `on`은 일부 파서에서 boolean `true`로 해석될 수 있습니다.  
이 파일에서는 `"on":` (따옴표 포함)을 사용해 문자열 키로 강제합니다 — 이것이 안전한 형태입니다.

**3. schedule과 workflow_dispatch가 같은 레벨에 있어야 한다**

```yaml
"on":           # 최상위
  schedule:     # "on" 아래 (같은 레벨)
    - cron: '...'
  workflow_dispatch:  # "on" 아래 (같은 레벨)
```

**4. cron은 항상 UTC 기준이다**

| KST 시각 | UTC 변환 | cron 표기 |
|---------|---------|---------|
| 09:00 KST | 00:00 UTC | `0 0 * * *` |
| 12:48 KST | 03:48 UTC | `48 3 * * *` |
| 15:48 KST | 06:48 UTC | `48 6 * * *` |

KST = UTC + 9. cron 설정 시 반드시 9시간을 빼서 UTC로 변환.

**5. 워크플로우가 disabled 상태가 아닌지**  
60일 이상 저장소 활동이 없으면 GitHub가 scheduled workflow를 자동으로 비활성화합니다.

확인 경로: **GitHub 저장소 → Actions 탭 → 왼쪽 사이드바 "Daily Monitoring" 클릭 → 오른쪽 상단 "..." 메뉴 → "Enable workflow"** 버튼이 보이면 비활성화된 상태입니다. 클릭해서 재활성화합니다.

---

### Schedule 실행 확인 방법

schedule로 자동 실행됐는지 확인하려면 실행 목록에서 **Event 컬럼이 `schedule`**인지 확인합니다.

```
GitHub 저장소 → Actions → Daily Monitoring
→ 실행 목록 중 Event 가 "schedule" 인 항목 확인
  (수동 실행은 "workflow_dispatch"로 표시됨)
```

---

### Schedule 실행이 안 될 때 순서대로 확인

1. **워크플로우 파일이 `main` 브랜치에 push됐는지 확인**  
   push 이전에 지난 cron 시간은 소급 실행되지 않습니다.  
   예: `0 0 * * *`을 23:50 UTC에 push하면 당일 00:00은 이미 지났으므로 내일 00:00부터 실행됩니다.

2. **workflow가 disabled 상태인지 확인** (위 5번 참조)

3. **cron이 UTC 기준으로 올바른지 확인**  
   `0 0 * * *` = UTC 00:00 = KST 09:00 ✓

4. **실행 시간 지연 허용**  
   GitHub 서버 부하 시 scheduled workflow는 예정 시각보다 **10~30분 지연**될 수 있습니다.  
   특히 정각(`:00`)은 부하가 집중되므로, 테스트 cron은 `:48`, `:23`처럼 비정각 분으로 설정을 권장합니다.  
   schedule 확인 시 **최소 20분은 기다린 후** Actions 탭을 확인합니다.

5. **Actions 탭에서 실행 기록이 없으면 re-enable 후 dummy push**

```bash
# 아무 변경이나 push해서 GitHub가 워크플로우를 재인식하게 함
git commit --allow-empty -m "chore: trigger workflow re-recognition"
git push origin main
```

---

### 임시 테스트 cron 추가 방법

schedule이 동작하는지 빠르게 확인하려면 현재 시각 기준 10~15분 뒤에 cron을 추가합니다.

```bash
# 현재 UTC 시각 확인
date -u +"%H:%M"
# 예: 06:33 UTC → 15:33 KST
# 15분 뒤 → 06:48 UTC = 15:48 KST
# cron: '48 6 * * *'
```

테스트 후 해당 cron 줄을 반드시 제거합니다.

---

## GitHub Secrets 등록

리포지터리 Settings → Secrets and variables → Actions → **New repository secret** 에서 아래 **4개**를 등록합니다.

| Secret 이름 | 값 | 비고 |
|---|---|---|
| `GOOGLE_SHEET_ID` | Google Sheets URL의 `/d/` 뒤 문자열 | `.env`의 `GOOGLE_SHEET_ID` 값과 동일 |
| `GOOGLE_CHAT_WEBHOOK_URL` | Google Chat 수신 Webhook URL | `.env`의 `GOOGLE_CHAT_WEBHOOK_URL` 값과 동일 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 서비스 계정 JSON 파일 전체 내용 | 아래 참조 |
| `AIRDNA_STATE_PASSPHRASE` | AirDNA 세션 암호화 passphrase | AirDNA GPG 암호화 시 직접 설정한 값 |

> **제거된 Secret**: `AIRDNA_STORAGE_STATE_JSON` — JSON 전체를 Secret으로 저장하는 방식은  
> GitHub Secret 크기 제한(64 KB)으로 인해 사용하지 않습니다. GPG 암호화 방식으로 대체됩니다.

### 서비스 계정 JSON 등록 방법

```bash
# 로컬에서 실행 — 출력 내용 전체를 GOOGLE_SERVICE_ACCOUNT_JSON Secret 값으로 붙여넣기
cat .secrets/google_service_account.json
```

> **중요**: 서비스 계정 이메일을 Google Sheet에 **편집자**로 공유해야 합니다.  
> 서비스 계정 이메일은 Actions 로그의 `[google_sheets] Service account email:` 줄에서 확인할 수 있습니다.  
> Google Sheets → 공유 → 해당 이메일 추가 → 편집자 권한 부여.

---

## AirDNA 세션 암호화 방법 (최초 설정 및 세션 갱신 시)

AirDNA 세션 파일(`.auth/airdna_state.json`)은 크기가 커서 GitHub Secret에 직접 저장할 수 없습니다.  
GPG 대칭 암호화를 사용하여 암호화된 파일(`secrets/airdna_state.json.gpg`)을 repo에 커밋합니다.  
복호화 passphrase만 GitHub Secret(`AIRDNA_STATE_PASSPHRASE`)으로 관리합니다.

### 1단계: passphrase 결정

안전한 임의 문자열을 하나 정합니다 (예: `openssl rand -hex 32` 출력값).  
이 값을 GitHub Secret `AIRDNA_STATE_PASSPHRASE`에 등록합니다.

### 2단계: 세션 갱신 (만료 시마다 반복)

```bash
# 로컬에서 실행
source .venv/bin/activate
python scripts/setup_airdna_session.py
```

스크립트가 브라우저를 열면 AirDNA에 수동 로그인 후 엔터를 누릅니다.  
`.auth/airdna_state.json`이 갱신됩니다.

### 3단계: 암호화 후 커밋

```bash
# secrets/ 디렉토리가 없으면 생성
mkdir -p secrets

# 암호화 (YOUR_PASSPHRASE 자리에 실제 passphrase 입력)
gpg --batch --yes --symmetric \
    --cipher-algo AES256 \
    --passphrase "YOUR_PASSPHRASE" \
    --output secrets/airdna_state.json.gpg \
    .auth/airdna_state.json

# 정상 생성 확인
ls -lh secrets/airdna_state.json.gpg

# 커밋
git add secrets/airdna_state.json.gpg
git commit -m "chore: refresh AirDNA session"
git push
```

> `.auth/airdna_state.json`(원본)은 `.gitignore`에 의해 커밋에서 제외됩니다.  
> `secrets/airdna_state.json.gpg`(암호화본)만 커밋합니다.

### 복호화 확인 (선택)

```bash
gpg --batch --yes --pinentry-mode loopback \
    --passphrase "YOUR_PASSPHRASE" \
    --decrypt --output /tmp/test_airdna.json \
    secrets/airdna_state.json.gpg

# 원본과 동일한지 확인
diff .auth/airdna_state.json /tmp/test_airdna.json && echo "OK"
```

---

## Google Sheet 연결 진단

실행 로그에서 아래 항목을 순서대로 확인합니다.

### 확인 1: 인증 파일 복원 (Verify credentials file step)

```
[debug] file exists: YES
[debug] file size:   2400 bytes
[debug] service_account_email: cgm-bot@your-project.iam.gserviceaccount.com
```

- `file exists: NO` → `GOOGLE_SERVICE_ACCOUNT_JSON` Secret이 비어 있거나 등록되지 않음
- `parse error` → Secret 값이 올바른 JSON이 아님 (복사 시 잘림 또는 따옴표 문제)

### 확인 2: Python 코드 내 진단 로그

Python 프로세스 시작 후 첫 번째 Sheets 호출 시 아래 로그가 출력됩니다:

```
[google_sheets] GOOGLE_APPLICATION_CREDENTIALS='.secrets/google_service_account.json' exists=True
[google_sheets] Credentials file size: 2400 bytes
[google_sheets] Service account email: cgm-bot@your-project.iam.gserviceaccount.com
```

- `exists=False` → 워크플로우의 "Create required directories" 이전에 파일 복원이 실패한 것

### 확인 3: append 성공 여부

행이 정상적으로 기록되면:
```
[google_sheets] append_row OK → tab='raw_supply_snapshots'
```

실패하면:
```
[google_sheets] Sheets API error 403: ...
```

### 공통 실패 원인

| 증상 | 원인 | 조치 |
|---|---|---|
| `403: The caller does not have permission` | 서비스 계정이 스프레드시트에 공유되지 않음 | Sheet에 서비스 계정 이메일을 편집자로 공유 |
| `403: UNAUTHENTICATED` 또는 JWT 에러 | 서비스 계정 JSON이 잘못 붙여넣어짐 | Secret 재등록 |
| `404: Requested entity was not found` | `GOOGLE_SHEET_ID`가 틀림 | Sheet URL에서 ID 재확인 |
| `file exists: NO` | `GOOGLE_SERVICE_ACCOUNT_JSON` 미등록 | Secret 등록 |
| AirDNA `login_required` | 세션 만료 | AirDNA 세션 갱신 절차 재실행 |
| `ModuleNotFoundError` | `requirements.txt` 누락 패키지 | 로컬 `pip freeze`로 확인 후 추가 |
| Playwright timeout | 사이트 응답 지연 | workflow_dispatch로 재시도 |

---

## 워크플로우 구조

```
checkout
  → Python 3.11 + pip cache
  → pip install -r requirements.txt
  → playwright install chromium
  → mkdir (.auth, .secrets, data/snapshots/*)
  → printf SA_JSON > .secrets/google_service_account.json
  → [Verify credentials file]  ← 진단 로그
  → gpg decrypt secrets/airdna_state.json.gpg → .auth/airdna_state.json
  → python -m src.jobs.run_daily
       ├─ collect_meta_ad_start_dates  (Meta 광고)
       ├─ collect_supply               (방 개수)
       ├─ detect_policy_changes        (정책/공지)
       └─ send_google_chat_message     (Korean 요약 발송)
```
