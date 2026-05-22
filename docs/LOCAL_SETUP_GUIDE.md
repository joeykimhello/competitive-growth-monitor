# 로컬 설치 및 수동 실행 가이드

경쟁사 모니터링 파이프라인을 **새 컴퓨터에서 로컬로 실행**하기 위한 완전한 설치 가이드.

---

## 1. 이 프로젝트가 하는 일

아래 데이터를 수집해서 **Google Sheet에 저장**하고 **Google Chat으로 한국어 요약**을 전송한다.

| 수집 항목 | 저장 탭 |
|---|---|
| Meta 광고 (현재 게재 수, 소재 목록) | `meta_ad_counts`, `meta_ad_start_dates` |
| Meta 30일 초과 장기 게재 광고 | `meta_ad_start_dates` (ad_start_date 기준) |
| 공개방 수 (Airbnb·리브애니웨어·엔코스테이·삼삼엠투) | `raw_supply_snapshots` |
| 정책/공지 변경 | `policy_updates` |
| 앱 버전 업데이트 | `app_versions` |
| 앱 설명 변경 | `app_versions` (설명 컬럼) |

실행 로그는 `run_log` 탭에 기록된다.

---

## 2. 사전 요구사항

### Python

**Python 3.11 권장** (3.9 이상 동작하나, 3.11 기준으로 개발·테스트됨)

```bash
python3 --version   # 3.11.x 확인
```

Python 3.11이 없으면 [python.org](https://www.python.org/downloads/) 또는 pyenv로 설치.

### Node.js

불필요. 이 프로젝트의 수집 파이프라인은 순수 Python이다.

### Chromium (Playwright)

Meta 광고, AirDNA, 앱스토어, 정책 페이지 수집에 Playwright Chromium을 사용한다. 별도 Chrome 설치 불필요—Playwright가 자체 Chromium을 내려받는다.

---

## 3. 프로젝트 클론 및 가상환경 세팅

```bash
# 1. 프로젝트 폴더로 이동 (이미 clone되어 있는 경우 생략)
cd ~/competitive-growth-monitor

# 2. 가상환경 생성
python3 -m venv .venv

# 3. 가상환경 활성화
source .venv/bin/activate   # macOS / Linux
# Windows: .venv\Scripts\activate

# 4. 의존성 설치
pip install -r requirements.txt

# 5. Playwright Chromium 브라우저 설치
python -m playwright install chromium
```

> 이후 모든 명령은 `.venv`가 활성화된 상태에서 실행한다.

---

## 4. 환경변수 / 비밀값 세팅

### 4-1. `.env` 파일 생성

프로젝트 루트에 `.env` 파일을 만든다 (`.env.example`을 복사해서 시작).

```bash
cp .env.example .env
```

`.env`에 아래 3개 값을 채운다.

```dotenv
# Google Sheets — 서비스 계정 JSON 파일 경로 (절대경로 또는 프로젝트 상대경로)
GOOGLE_APPLICATION_CREDENTIALS=.secrets/google_service_account.json

# Google Spreadsheet ID (URL의 /spreadsheets/d/<ID>/edit 부분)
GOOGLE_SHEET_ID=your_spreadsheet_id_here

# Google Chat Incoming Webhook URL
GOOGLE_CHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/YOUR_SPACE/messages?key=...
```

> `.env.example`에 있는 `ENV`, `AUTOMATION_API_TOKEN` 항목은 수동 로컬 실행에 불필요하다.

### 4-2. 코드가 실제로 읽는 환경변수 이름

| 변수 | 용도 | 필수 |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | 서비스 계정 JSON 파일 경로 | ✅ |
| `GOOGLE_SHEET_ID` | 대상 스프레드시트 ID | ✅ |
| `GOOGLE_CHAT_WEBHOOK_URL` | Google Chat 수신 Webhook | ✅ |

> AirDNA 세션 경로는 환경변수가 아니라 코드에 하드코딩되어 있다: `.auth/airdna_state.json` (5항 참조)

---

## 5. Google Cloud / Google Sheets 권한 세팅

### 5-1. 서비스 계정 JSON 준비

1. [Google Cloud Console](https://console.cloud.google.com) → 프로젝트 선택 (예: `spacev-ai-tf`)
2. **APIs & Services → Enabled APIs** 에서 **Google Sheets API** 가 활성화되어 있는지 확인
3. **IAM & Admin → Service Accounts** 에서 기존 서비스 계정을 선택하거나 새로 생성
4. 키 탭 → **ADD KEY → JSON** → 다운로드
5. 받은 JSON을 프로젝트 로컬에 저장

```bash
mkdir -p .secrets
mv ~/Downloads/your-key-file.json .secrets/google_service_account.json
```

> `.secrets/`는 `.gitignore`에 포함되어 있어 git에 올라가지 않는다.  
> JSON 파일을 절대 커밋하거나 공유하지 않는다.

### 5-2. JSON 내용 확인

```bash
python3 -c "
import json
d = json.load(open('.secrets/google_service_account.json'))
print('email :', d.get('client_email'))
print('project:', d.get('project_id'))
"
```

`project_id`가 의도한 Google Cloud 프로젝트(예: `spacev-ai-tf`)인지 확인한다.

### 5-3. Google Sheet에 서비스 계정 공유

API 키만으로는 특정 스프레드시트에 쓸 수 없다. `client_email`을 스프레드시트에 **편집자**로 공유해야 한다.

1. 대상 Google Sheet 열기
2. 우상단 공유 버튼 클릭
3. `client_email` 값을 붙여넣고 **편집자** 권한으로 공유
4. "알림 보내기" 체크 해제 후 확인

---

## 6. Google Sheet 탭 준비

로컬 수동 실행에 필요한 탭은 아래 **6개**다.

| 탭 이름 | 설명 |
|---|---|
| `meta_ad_counts` | 경쟁사별 Meta 광고 수 일별 기록 |
| `meta_ad_start_dates` | 광고 소재별 게재 시작일 및 크리에이티브 |
| `raw_supply_snapshots` | 공개방 수 수집 원본 |
| `policy_updates` | 정책/공지 페이지 변경 감지 |
| `app_versions` | 앱 버전 및 설명 변경 |
| `run_log` | 실행 로그 |

첫 실행 전 각 탭이 없으면 자동으로 헤더 행을 생성한다.  
탭이 없는 경우 Google Sheet에서 수동으로 탭을 추가하면 된다 (이름 정확히 일치 필요).

### `app_versions` 탭 — 앱 설명 변경 감지 컬럼

앱 설명 변경 감지를 위해 아래 4개 컬럼이 `app_versions` 탭에 **수동으로 추가**되어 있어야 한다.  
(sheet_schema.yaml 스키마 외 컬럼이므로 직접 헤더 행에 추가한다.)

| 컬럼 이름 | 내용 |
|---|---|
| `app_description` | 앱스토어 앱 설명 전문 |
| `app_description_hash` | 설명 해시 (SHA-256 앞 16자) |
| `is_description_changed` | 전일 대비 설명 변경 여부 (True/False) |
| `description_change_summary_ko` | 변경 요약 (한국어) |

추가 방법: `app_versions` 탭 헤더 행에서 기존 마지막 컬럼 오른쪽에 위 4개 이름을 그대로 입력한다.

---

## 7. AirDNA 세션 세팅

Airbnb 서울 공급량(Total Active Listings) 수집은 **AirDNA 로그인 세션**에 의존한다.  
세션 파일 없이 실행하면 Airbnb 공급량 수집이 `login_required` 상태로 실패한다.

### 7-1. 최초 세션 저장

```bash
source .venv/bin/activate
python scripts/setup_airdna_session.py
```

실행하면:
1. 실제 Chromium 브라우저 창이 열리고 AirDNA 서울 listings 페이지로 이동한다
2. 브라우저에서 AirDNA 계정으로 로그인한다
3. 서울 listings 페이지에서 "Total Active Listings" 숫자가 보이는지 확인한다
4. 터미널로 돌아와 **Enter** 를 누른다

세션이 `.auth/airdna_state.json`에 저장된다.

### 7-2. 세션 만료 시 재갱신

아래 증상이 나타나면 세션이 만료된 것이다:
- Google Sheet `raw_supply_snapshots`에서 airbnb 행의 `status = login_required`
- Google Chat [공개방 수]에 Airbnb 서울 미표시

재갱신:

```bash
python scripts/setup_airdna_session.py
```

동일하게 브라우저 로그인 후 Enter.

### 7-3. 주의사항

- `.auth/airdna_state.json`에는 로그인 쿠키와 토큰이 포함된다
- **절대 커밋하거나 공유하지 않는다** (`.gitignore`에 `.auth/`가 포함됨)
- 타인과 공유 시 계정 보안 위협 발생

---

## 8. 로컬 수동 실행

### 8-1. 전체 파이프라인 실행 (권장)

```bash
cd ~/competitive-growth-monitor
source .venv/bin/activate
bash scripts/run_daily_local.sh
```

실행 순서: Meta 광고 → 공급량 → 정책/공지 → 앱 버전 → run_log 기록 → Google Chat 발송

로그 파일로 저장하려면:

```bash
bash scripts/run_daily_local.sh >> ~/cgm-daily.log 2>&1
```

### 8-2. 개별 워크플로우만 실행

```bash
# Meta 광고 수집만
python -m src.jobs.collect_meta_ad_start_dates

# 공급량(방 개수) 수집만
python -m src.jobs.collect_supply

# 정책/공지 변경 감지만
python -m src.jobs.detect_policy_changes

# 앱 버전 수집만
python -m src.jobs.collect_app_versions
```

> 개별 실행 시 Google Chat 발송은 없다. Chat은 `run_daily.py` (전체 실행)에서만 발송된다.

---

## 9. 실행 후 확인

### Google Sheet 확인 항목

| 탭 | 확인 내용 |
|---|---|
| `run_log` | 가장 최근 행의 `status` = `ok` 또는 `partial` 확인 |
| `meta_ad_counts` | 오늘 날짜로 8개 경쟁사 행 추가되었는지 확인 |
| `meta_ad_start_dates` | 오늘 날짜 광고 소재 행들 추가 확인 |
| `raw_supply_snapshots` | 오늘 날짜 supply 행 확인 (airbnb/서울 포함) |
| `policy_updates` | 정책 페이지 체크 결과 확인 |
| `app_versions` | 각 앱별 버전 및 설명 확인 |

### Google Chat 확인 항목

**항상 표시:**
- `[Meta 광고]` — 경쟁사별 현재 광고 수 및 전일 대비
- `[Meta 30일 초과 광고]` — 한 달 넘게 게재 중인 광고 수
- `[공개방 수]` — Airbnb·리브애니웨어·엔코스테이·삼삼엠투 공개방 수

**변경 있을 때만 표시:**
- `[정책/공지]` — 새 공지 또는 내용 변경 감지 시
- `[앱 업데이트]` — 신규 버전 출시 감지 시
- `[앱 설명 변경]` — 앱 설명 변경 감지 시
- `[시트 기록 실패 - Meta 광고 수]` — meta_ad_counts 저장 실패 시 (Google Sheets API 429 등)

---

## 10. 자주 나는 오류와 해결

### Google Sheets API 권한 오류

```
google.api_core.exceptions.PermissionDenied: 403
```

체크리스트:
- 서비스 계정 `client_email`이 해당 Google Sheet에 **편집자**로 공유되어 있는지 확인
- `.env`의 `GOOGLE_APPLICATION_CREDENTIALS` 경로가 올바른지 확인
- JSON의 `project_id`가 Google Sheets API가 활성화된 프로젝트인지 확인
- Google Cloud Console에서 **Google Sheets API** 가 사용 설정되어 있는지 확인

### Google Sheets API 429 — Quota 오류

```
Sheets API error 429: Quota exceeded for quota metric 'Write requests'
```

원인: 단기간에 API 쓰기 요청이 60회/분을 초과.  
대응: 코드에 자동 retry 로직(최대 3회, 20/40/80초 대기)이 내장되어 있다.  
계속 발생하면 20~30분 후 재실행한다.

### AirDNA 세션 만료

```
[LOGIN_REQUIRED] airbnb/airdna / Seoul
```

또는 Google Sheet에서 airbnb 행 `status = login_required`.

해결:

```bash
python scripts/setup_airdna_session.py
```

브라우저에서 AirDNA 재로그인 후 Enter.

### Playwright Chromium 없음

```
playwright._impl._errors.Error: Executable doesn't exist at ...
```

해결:

```bash
python -m playwright install chromium
```

### Google Chat 메시지가 안 옴

체크리스트:
- `.env`에 `GOOGLE_CHAT_WEBHOOK_URL`이 올바르게 설정되어 있는지 확인
- URL이 `https://chat.googleapis.com/v1/spaces/...` 형식인지 확인
- `run_log` 탭에 오늘 날짜 행이 있는지 확인 (파이프라인 자체가 실행됐는지 판단)
- 개별 job 실행(`python -m src.jobs.collect_meta_ad_start_dates` 등)으로는 Chat이 발송되지 않음

---

## 11. 민감 파일 위치 정리

| 파일 | 위치 | git 포함 여부 |
|---|---|---|
| 서비스 계정 JSON | `.secrets/google_service_account.json` | ❌ (`.gitignore`) |
| 환경변수 | `.env` | ❌ (`.gitignore`) |
| AirDNA 세션 | `.auth/airdna_state.json` | ❌ (`.gitignore`) |
| 수집 스냅샷 | `data/snapshots/` | ❌ (`.gitignore`) |

`.secrets/`, `.auth/`, `.env`는 **절대 커밋하지 않는다.**

---

## 12. 핵심 설치 순서 요약

```
1. python3 -m venv .venv && source .venv/bin/activate
2. pip install -r requirements.txt
3. python -m playwright install chromium
4. .secrets/google_service_account.json 준비
5. cp .env.example .env  →  3개 값 채우기
   (GOOGLE_APPLICATION_CREDENTIALS / GOOGLE_SHEET_ID / GOOGLE_CHAT_WEBHOOK_URL)
6. Google Sheet에 서비스 계정 client_email 편집자 공유
7. python scripts/setup_airdna_session.py  (브라우저 로그인 → Enter)
8. bash scripts/run_daily_local.sh
```
