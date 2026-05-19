You are helping with **competitive-growth-monitor**, a Korean competitive intelligence system that runs daily and reports to Google Chat and Google Sheets.

---

## 프로젝트 핵심 정보

**모니터링 대상 경쟁사:**
- Airbnb, LiveAnywhere (리브애니웨어), Encostay (엔코스테이)
- 자리톡 (zaristay), 직방 (zigbang), 미스터멘션 (mister_mention)
- 삼삼엠투1 (33m2_1), 삼삼엠투2 (33m2_2)

**수집 항목:**
1. Meta 광고 게재일 (meta_ad_start_dates) — Facebook Ad Library 스크래핑
2. 공급량 (raw_supply_snapshots) — AirDNA, LiveAnywhere, Encostay 웹
3. 정책/공지 (policy_updates) — 각사 공지 페이지
4. 앱 버전 (app_versions) — iTunes Lookup API + Play Store Playwright

**실행 명령:**
```bash
source .venv/bin/activate

# 전체 일일 실행
python -m src.jobs.run_daily

# 개별 수집
python -m src.jobs.collect_meta_ad_start_dates
python -m src.jobs.collect_supply
python -m src.jobs.detect_policy_changes
python -m src.jobs.collect_app_versions
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
    meta_ad_library.py      # Playwright — Facebook Ad Library 스크래핑
    google_ads_transparency.py  # Playwright — Google 광고 투명성 센터
  supply/
    airbnb.py               # Playwright — AirDNA (로그인 세션 필요)
    liveanywhere.py         # Playwright — LiveAnywhere 검색결과
    encostay.py             # Playwright — Encostay 지도 검색
  apps/
    itunes_lookup.py        # requests — iTunes Lookup API (API key 불필요)
    google_play.py          # Playwright — Play Store 공개 페이지

src/jobs/
  run_daily.py              # 전체 오케스트레이터 (Meta → Supply → Policy → Apps → Chat)
  collect_meta_ad_start_dates.py  # Meta 광고 수집 + 노란색 하이라이트
  collect_supply.py         # 방 개수 수집
  detect_policy_changes.py  # 정책/공지 변경 감지
  collect_app_versions.py   # 앱 버전 수집

src/integrations/
  google_sheets.py          # Sheets API 래퍼 (append_row, read_sheet_rows 등)
  google_chat.py            # Google Chat Webhook 발송

.github/workflows/
  daily-monitoring.yml      # GitHub Actions — 매일 00:00 UTC (09:00 KST)

secrets/
  airdna_state.json.gpg     # AirDNA 세션 (GPG 암호화, 커밋됨)
```

---

## Google Sheets 탭

| 탭 | 기록 주체 | 주요 컬럼 |
|---|---|---|
| `meta_ad_start_dates` | collect_meta_ad_start_dates | competitor, library_id, ad_start_date |
| `meta_ad_counts` | collect_meta_ad_start_dates | date, competitor, displayed_meta_count |
| `raw_supply_snapshots` | collect_supply | competitor, region, count |
| `policy_updates` | detect_policy_changes | competitor, latest_title, is_new, is_changed |
| `app_versions` | collect_app_versions | competitor, platform, version, release_date |
| `run_log` | run_daily | run_started_at, failed_count, status |

---

## Google Chat 리포트 구조 (한국어)

```
*경쟁사 모니터링 일일 리포트* (YYYY-MM-DD)

*[Meta 광고]*
• Airbnb: 120개(+1개)
• LiveAnywhere: 24개(-1개)
...

*[Meta 30일 초과 광고]*
• Airbnb: 3개
...

*[공개방 수]*
• Airbnb: 서울 1,234개
...

*[정책/공지]*
• Airbnb: 변경 없음
...

*[앱 업데이트]*
• Airbnb: IOS 24.18 / ANDROID 24.18
• 리브애니웨어: IOS 2.1.0 ★신규 / ANDROID 2.1.0 ★신규
...

실패: N건 | 완료: YYYY-MM-DD HH:MM UTC
```

---

## 주요 설계 원칙

- **displayed_meta_count** = 페이지 헤더의 "결과 ~80개" 수치 (Google Chat에 표시)
- **long_running_count** = ad_start_date < collection_date − 1개월인 광고 수
- **노란색 셀** = 동일 조건 (collect_meta_ad_start_dates가 자동 적용)
- **AirDNA 세션** = `.auth/airdna_state.json` (로그인 필요, GPG로 repo 관리)
- **Playwright 스크롤** = `_scroll_adaptive()` — target 도달 또는 3회 no-growth 시 중단
- **Supply에서 자리톡 제외** = 수집 불안정으로 collect_supply에서 제거됨
- **33m2는 Meta 2계정** = 33m2_1 (page_id=532282707266733), 33m2_2 (page_id=936539016218927)

---

## 환경 변수 (`.env` 또는 GitHub Secrets)

| 이름 | 용도 |
|---|---|
| `GOOGLE_SHEET_ID` | 수집 데이터가 저장되는 스프레드시트 ID |
| `GOOGLE_APPLICATION_CREDENTIALS` | 서비스 계정 JSON 파일 경로 (`.secrets/google_service_account.json`) |
| `GOOGLE_CHAT_WEBHOOK_URL` | 일일 요약 발송 Webhook |
| `AIRDNA_STATE_PASSPHRASE` | GPG passphrase (GitHub Actions에서 세션 복호화) |

---

## 자주 하는 작업 가이드

**Meta 카드가 적게 수집될 때:**
`src/collectors/ads/meta_ad_library.py`의 `_MAX_SCROLLS`(현재 15) 또는 `_SCROLL_PAUSE_MS`(현재 2000)를 조정한다.

**AirDNA 세션 만료 시:**
```bash
python scripts/setup_airdna_session.py
gpg --batch --symmetric --cipher-algo AES256 \
    --passphrase "YOUR_PASSPHRASE" \
    --output secrets/airdna_state.json.gpg .auth/airdna_state.json
git add secrets/airdna_state.json.gpg && git commit -m "chore: refresh AirDNA session"
```

**새 경쟁사 추가 시 체크리스트:**
1. `config/supply_sources.yaml` — 방 개수 URL 추가
2. `config/policy_pages.yaml` — 공지 URL 추가
3. `src/jobs/collect_meta_ad_start_dates.py` — `_COMPETITORS` 딕셔너리에 추가
4. `src/jobs/run_daily.py` — `_META_ORDER`, `_POLICY_ORDER`, `_SUPPLY_DISPLAY` 갱신
5. `config/app_sources.yaml` — iOS App ID / Android 패키지명 추가
6. Playwright 수집이 필요하면 `src/collectors/supply/` 또는 `src/collectors/apps/`에 신규 파일 추가

**Google Sheet 403 에러 시:**
Actions 로그의 `[google_sheets] Service account email:` 줄 이메일을 확인하고, 해당 이메일을 Google Sheet에 편집자로 공유한다.
