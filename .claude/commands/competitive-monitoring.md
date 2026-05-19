You are helping with **competitive-growth-monitor**, a Korean competitive intelligence system.

> **다음에 Claude가 이 프로젝트를 이어서 작업할 때 반드시 이 문서를 먼저 읽을 것.**

---

## 프로젝트 목적

경쟁사 성장/마케팅/공급/정책/앱 업데이트 모니터링 자동화.
- 수집 데이터 → Google Sheets 누적 저장
- Google Chat → 한국어 일일 리포트 발송
- GitHub Actions → 매일 09:00 KST 자동 실행

---

## 주요 실행 명령

```bash
source .venv/bin/activate

# 전체 실행
bash scripts/run_daily_local.sh

# 개별 수집
python -m src.jobs.collect_meta_ad_start_dates
python -m src.jobs.collect_supply
python -m src.jobs.detect_policy_changes
python -m src.jobs.collect_app_versions

# 특정 앱만
python -m src.jobs.collect_app_versions --competitor 33m2 --platform android
python -m src.jobs.collect_app_versions --competitor zigbang
```

---

## 디렉토리 구조

```
config/
  app_sources.yaml          # iOS App ID + Android 패키지명
  supply_sources.yaml       # 방 개수 수집 URL
  policy_pages.yaml         # 정책/공지 페이지 URL + 타입
  sheet_schema.yaml         # Google Sheets 탭 컬럼 정의
  competitors.yaml          # 경쟁사 기본 정보

src/collectors/
  ads/
    meta_ad_library.py      # Playwright — Facebook Ad Library
  supply/
    airbnb.py               # Playwright — AirDNA (로그인 세션 필요)
    liveanywhere.py         # Playwright — LiveAnywhere 검색결과
    encostay.py             # Playwright — Encostay 지도 검색
  apps/
    itunes_lookup.py        # requests — iTunes Lookup API (no auth)
    google_play.py          # Playwright — Play Store 공개 페이지

src/jobs/
  run_daily.py              # 오케스트레이터 (Meta → Supply → Policy → Apps → Chat)
  collect_meta_ad_start_dates.py  # Meta 광고 수집
  collect_supply.py         # 방 개수 수집
  detect_policy_changes.py  # 정책/공지 변경 감지
  collect_app_versions.py   # 앱 버전 수집

src/integrations/
  google_sheets.py          # Sheets API 래퍼
  google_chat.py            # Google Chat Webhook 발송

.github/workflows/
  daily-monitoring.yml      # GitHub Actions 스케줄

secrets/
  airdna_state.json.gpg     # AirDNA 세션 (GPG 암호화, 커밋됨)
```

---

## Google Sheets 탭

| 탭 | 기록 주체 | 역할 |
|---|---|---|
| `meta_ad_start_dates` | collect_meta_ad_start_dates | 광고별 라이브러리 ID, URL, 게재 시작일 |
| `meta_ad_counts` | collect_meta_ad_start_dates | 날짜별/경쟁사별 Meta 화면상 광고 총개수 (전일 대비 기준) |
| `raw_supply_snapshots` | collect_supply | Airbnb/LiveAnywhere/Encostay 공개방 수 |
| `policy_updates` | detect_policy_changes | 정책/공지 최신값 및 변경 여부 |
| `app_versions` | collect_app_versions | 앱스토어/구글플레이 버전, 업데이트 날짜, 릴리즈 노트 |
| `run_log` | run_daily | 실행 결과 로그 |

---

## Meta 광고 수집 규칙

**Google Ads는 현재 제외. Meta Ad Library만 사용.**

### displayed_meta_count vs written
- `displayed_meta_count` = 화면 상단 "결과 ~N개" 텍스트에서 파싱한 값
- `written` = 해당 실행에서 `meta_ad_start_dates`에 기록된 행 수
- **이 둘을 절대 혼동하지 않는다**
- Google Chat `[Meta 광고]`에는 반드시 `displayed_meta_count` 사용

### 전일 대비 증감 계산
- 기준 탭: `meta_ad_counts`
- `today_count` = 오늘 실행에서 수집한 `displayed_meta_count`
- `previous_count` = 가장 최근 이전 calendar date의 row (status=failed 제외)
- 같은 날짜 이전 실행 row는 비교 대상에서 제외
- 이전 날짜 데이터 없을 때만 "(신규 기준)" 표시

### 30일 초과 광고
- 기준 탭: `meta_ad_start_dates`
- 조건: `ad_start_date < collection_date - 1 calendar month`
- 정확히 한 달된 날은 **포함하지 않음** (strictly less than)

### Meta 광고 대상 (8계정)
| key | 표시명 |
|---|---|
| airbnb | Airbnb |
| liveanywhere | LiveAnywhere |
| encostay | Encostay |
| zaristay | 자리톡 |
| zigbang | 직방 |
| mister_mention | 미스터멘션 |
| 33m2_1 | 삼삼엠투1 |
| 33m2_2 | 삼삼엠투2 |

---

## 공개방 수 수집 규칙

| 경쟁사 | 방법 | 비고 |
|---|---|---|
| Airbnb | AirDNA Seoul Total Active Listings | 로그인 세션 필요 |
| LiveAnywhere | 국내/서울 검색결과 N건 | Playwright |
| Encostay | map-view estimate 국내 | Playwright |

- **자리톡 supply는 현재 제외** (수집 불안정)
- AirDNA 세션 만료 시: `python scripts/setup_airdna_session.py` → GPG 재암호화 → 커밋

---

## 정책/공지 수집 규칙

대상: Airbnb, 리브애니웨어, 엔코스테이, 미스터멘션, 직방, 삼삼엠투
**자리톡 정책/공지는 현재 제외.**

- 목록 페이지 파싱 → 최신 게시물 상세 페이지 내용 저장
- 이전 수집과 비교해 `is_new` / `is_changed` 플래그 기록
- LiveAnywhere: Playwright `_playwright_fetch_liveanywhere` 사용
- Encostay (Zendesk): `js_rendered: true` + Playwright generic fetch

---

## 앱 업데이트 수집 규칙

### iOS
- iTunes Lookup API 사용 (API key 불필요)
- 국가 코드: `kr`

### Android
- Google Play PC 웹 페이지 Playwright 파싱 (`hl=ko&gl=KR`)
- **앱 정보 팝업** (`앱 정보 자세히 알아보기` 버튼 클릭) → 버전/업데이트 날짜 수집
- **새로운 기능** 섹션 → `release_notes` 수집
- `status=ok` 조건: `version OR release_date` 둘 중 하나라도 있으면 ok
- Airbnb/직방 Android: `새로운 기능` 섹션이 headless에서 안 잡힐 수 있음 → `release_notes` 빈칸 허용

### 앱 대상 (7개, iOS + Android 각각 수집)
airbnb, liveanywhere, encostay, zaristay, zigbang, mister_mention, 33m2

### 변경 감지
- `is_new_version`: version 문자열 변경 시 TRUE
- `is_changed`: version, release_date, release_notes 중 하나라도 변경 시 TRUE
- `change_summary_ko`: 변경 내용 한국어 요약

---

## Google Chat 리포트 구조

```
*경쟁사 모니터링 일일 리포트* (YYYY-MM-DD)

*[Meta 광고]*
• Airbnb: 120개(+1개)
• LiveAnywhere: 24개(-1개)
...

*[Meta 30일 초과 광고]*
• Airbnb: 8개
...

*[공개방 수]*
• Airbnb: 서울 1,234개
• 리브애니웨어: 서울 N개, 국내 N개
• 엔코스테이: 국내 N개

*[정책/공지]*
• Airbnb: 변경 없음
• 리브애니웨어: 신규
...

*[앱 업데이트]*
• Airbnb iOS: v26.20 / 변경 없음
• Airbnb Android: v26.19 / 변경 없음
• 리브애니웨어 iOS: v3.11.4 / 업데이트 감지 - 버전 변경: 3.11.3 -> 3.11.4
...

실패: N건 | 완료: YYYY-MM-DD HH:MM UTC
```

---

## GitHub Actions 배포

파일: `.github/workflows/daily-monitoring.yml`

| 방식 | cron | KST |
|---|---|---|
| 정기 실행 | `0 0 * * *` | 09:00 KST |
| 테스트 실행 | `30 3 * * *` | 12:30 KST (임시) |
| 수동 | workflow_dispatch | — |

### AirDNA 세션 관리
- 암호화된 `secrets/airdna_state.json.gpg`만 커밋 (GPG AES256)
- 복호화 passphrase → GitHub Secret `AIRDNA_STATE_PASSPHRASE`
- **raw `.auth/airdna_state.json`은 절대 커밋하지 않는다**

### GitHub Secrets (4개 필수)
| Secret | 내용 |
|---|---|
| `GOOGLE_SHEET_ID` | 스프레드시트 ID |
| `GOOGLE_CHAT_WEBHOOK_URL` | Chat Webhook URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 서비스 계정 JSON 전체 |
| `AIRDNA_STATE_PASSPHRASE` | GPG passphrase |

---

## 환경 변수 (`.env`)

| 이름 | 용도 |
|---|---|
| `GOOGLE_SHEET_ID` | 스프레드시트 ID |
| `GOOGLE_APPLICATION_CREDENTIALS` | 서비스 계정 JSON 경로 |
| `GOOGLE_CHAT_WEBHOOK_URL` | Chat Webhook URL |

---

## 주의사항 (커밋 금지 목록)

- `.env`
- `.auth/` (AirDNA raw session)
- `.secrets/` (service account JSON)
- `data/snapshots/` (Playwright 스냅샷)
- raw service account JSON 파일 (`*.json` credentials)

변경 후 push 전 반드시:
```bash
git status   # 민감 파일이 staged되지 않았는지 확인
```

---

## Known Issues

| 항목 | 내용 |
|---|---|
| 직방 Android release_notes | Playwright headless에서 새로운 기능 섹션이 안 잡힘 — 빈칸 허용, status=ok 유지 |
| 자리톡 Android version | "기기에 따라 다릅니다" → 버전 파싱 안 됨, release_date로 ok 처리 |
| AirDNA 간헐적 실패 | 로그인 세션 있어도 count_not_found 발생 가능 |
| GitHub schedule 소급 없음 | push 이후 cron부터 적용, 지나간 시간은 실행되지 않음 |
| meta_ad_counts 신규 기준 | 과거 데이터 없으면 신규 기준으로 표시, 다음 날부터 정상 |

---

## 자주 하는 작업

### AirDNA 세션 갱신
```bash
python scripts/setup_airdna_session.py
gpg --batch --yes --symmetric --cipher-algo AES256 \
    --passphrase "YOUR_PASSPHRASE" \
    --output secrets/airdna_state.json.gpg .auth/airdna_state.json
git add secrets/airdna_state.json.gpg
git commit -m "chore: refresh AirDNA session"
git push
```

### 새 경쟁사 추가 체크리스트
1. `config/app_sources.yaml` — iOS App ID / Android 패키지명
2. `config/supply_sources.yaml` — 방 개수 URL
3. `config/policy_pages.yaml` — 공지 URL
4. `src/jobs/collect_meta_ad_start_dates.py` — `_COMPETITORS` 딕셔너리
5. `src/jobs/run_daily.py` — `_META_ORDER`, `_APP_ORDER`, `_POLICY_ORDER`, `_SUPPLY_DISPLAY`

### Google Sheet 403 에러
Actions 로그의 `[google_sheets] Service account email:` 이메일을 확인하고, 해당 이메일을 Google Sheet 편집자로 공유.

### Meta 카드 수집 부족 시
`src/collectors/ads/meta_ad_library.py`의 `_MAX_SCROLLS`(현재 15) 또는 `_SCROLL_PAUSE_MS`(현재 2000) 조정.
