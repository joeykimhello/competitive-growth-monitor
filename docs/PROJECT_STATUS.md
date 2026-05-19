# PROJECT STATUS

최종 업데이트: 2026-05-19

---

## 1. 목표

경쟁사 성장/마케팅/공급/정책/앱 업데이트 모니터링 자동화 시스템.

- Meta 광고 집행, 공개방 수, 정책/공지 변화, 앱 버전을 매일 추적
- 수집 데이터 → Google Sheets 누적 저장
- Google Chat → 한국어 일일 리포트 발송
- GitHub Actions → 매일 09:00 KST 자동 실행

---

## 2. 운영 상태

| 항목 | 상태 | 비고 |
|---|---|---|
| Meta 광고 수집 | 운영 중 | 8개 계정, displayed_meta_count + 30일 초과 계산 |
| 공개방 수 수집 | 운영 중 | Airbnb(AirDNA), LiveAnywhere, Encostay |
| 정책/공지 수집 | 운영 중 | 6개 경쟁사 (자리톡 제외) |
| 앱 버전 수집 | 운영 중 | 7개 경쟁사 iOS + Android |
| Google Chat 리포트 | 운영 중 | 5개 섹션 |
| GitHub Actions | 운영 중 | 09:00 KST 정기, 12:30 KST 테스트 (임시) |

---

## 3. 모니터링 대상 경쟁사

| key | 표시명 | Meta | Supply | Policy | App |
|---|---|---|---|---|---|
| airbnb | Airbnb | 1계정 | AirDNA 서울 | O | iOS + Android |
| liveanywhere | 리브애니웨어 | 1계정 | 서울/국내 | O | iOS + Android |
| encostay | 엔코스테이 | 1계정 | 국내 | O | iOS + Android |
| zaristay | 자리톡 | 1계정 | 제외 | 제외 | iOS + Android |
| zigbang | 직방 | 1계정 | — | O | iOS + Android |
| mister_mention | 미스터멘션 | 1계정 | — | O | iOS + Android |
| 33m2_1 / 33m2_2 | 삼삼엠투 | 2계정 | — | O | iOS + Android (33m2 단일키) |

---

## 4. Google Sheets 탭 구성

| 탭 | 컬럼 수 | 역할 |
|---|---|---|
| `meta_ad_start_dates` | 13 | 광고별 라이브러리 ID, URL, 게재 시작일, 소재 텍스트 |
| `meta_ad_counts` | 8 | 날짜별 경쟁사별 화면상 광고 총개수 (전일 대비 기준) |
| `raw_supply_snapshots` | 13 | 공개방 수, 수집 방법, 신뢰도 |
| `policy_updates` | 16 | 최신 게시물, is_new, is_changed, 원문 텍스트 |
| `app_versions` | 16 | 버전, 출시일, 릴리즈 노트, 변경 여부 |
| `run_log` | 10 | 실행 시작/종료, 실패 수, 전체 상태 |

---

## 5. 실행 방법

```bash
source .venv/bin/activate

# 전체
bash scripts/run_daily_local.sh

# 개별
python -m src.jobs.collect_meta_ad_start_dates
python -m src.jobs.collect_supply
python -m src.jobs.detect_policy_changes
python -m src.jobs.collect_app_versions

# 필터 옵션
python -m src.jobs.collect_app_versions --competitor 33m2 --platform android
python -m src.jobs.collect_app_versions --competitor zigbang
```

---

## 6. Meta 광고 수집 규칙 (중요)

- **Google Ads 제외. Meta Ad Library만 사용.**
- `displayed_meta_count` ≠ `written` (row 수) — 절대 혼동 금지
- `displayed_meta_count`: 화면 상단 "결과 ~N개" 파싱값 → Chat 리포트에 사용
- `meta_ad_counts` 탭 기준으로 전일 대비 +N/-N 계산
- 30일 초과 광고: `ad_start_date < collection_date - 1 calendar month` (정확히 한 달은 제외)

---

## 7. 앱 수집 규칙 (중요)

- iOS: iTunes Lookup API (`country=kr`)
- Android: Google Play PC 웹 Playwright (`hl=ko&gl=KR`)
  - 앱 정보 팝업 → 버전/업데이트 날짜
  - 새로운 기능 섹션 → release_notes
  - `status=ok` 조건: version 또는 release_date 중 하나라도 존재
- 직방 Android: headless에서 새로운 기능 미수집 허용 (known limitation)
- 자리톡 Android: version "기기에 따라 다릅니다" → release_date로 ok 처리

---

## 8. GitHub Actions 스케줄

| 스케줄 | cron (UTC) | KST | 목적 |
|---|---|---|---|
| 정기 실행 | `0 0 * * *` | 09:00 | 매일 정기 |
| 테스트 실행 | `30 3 * * *` | 12:30 | schedule 동작 확인용 (임시) |

테스트 실행 확인 후 12:30 cron은 제거 예정.

---

## 9. 환경 변수 및 Secrets

| 이름 | 로컬 (`.env`) | GitHub Secret |
|---|---|---|
| `GOOGLE_SHEET_ID` | O | O |
| `GOOGLE_APPLICATION_CREDENTIALS` | O (파일 경로) | — |
| `GOOGLE_CHAT_WEBHOOK_URL` | O | O |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | — | O (JSON 전체) |
| `AIRDNA_STATE_PASSPHRASE` | — | O |

---

## 10. Known Issues

| 항목 | 내용 | 대응 |
|---|---|---|
| 직방 Android release_notes | headless Playwright에서 새로운 기능 섹션 미수집 | release_notes 빈칸 허용, status=ok 유지 |
| 자리톡 Android version | "기기에 따라 다릅니다" → 숫자 파싱 불가 | release_date 기준 ok 처리 |
| AirDNA 간헐적 실패 | 세션 있어도 count_not_found 발생 가능 | 재실행으로 해결 |
| meta_ad_counts 신규 기준 | 이전 날짜 데이터 없으면 "신규 기준" 표시 | 다음 날부터 정상 |
| GitHub schedule 소급 없음 | push 이후 cron만 적용 | 수동 실행으로 보완 |

---

## 11. 커밋 금지 목록

```
.env
.auth/
.secrets/
data/snapshots/
*.json  (credentials)
```

push 전: `git status`로 민감 파일 staged 여부 반드시 확인.

---

## 12. 다음 할 일

- [ ] 12:30 KST 테스트 schedule 확인 후 cron 제거
- [ ] meta_ad_counts 전일 대비 정상 동작 확인 (다음 날 리포트)
- [ ] Looker Studio 연결 (Google Sheets 데이터 시각화)
- [ ] AirDNA 세션 만료 주기 파악 및 자동 갱신 검토
