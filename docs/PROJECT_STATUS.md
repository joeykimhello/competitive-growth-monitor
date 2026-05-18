# PROJECT STATUS

현재 로컬 MVP 기준 프로젝트 상태 문서.

---

## 1. 목표

- 경쟁사 성장률 및 정책 변경 분석 자동화 시스템
- 경쟁사(Airbnb, LiveAnywhere, Encostay)의 광고 집행, 공급 물량, 정책/공지 변화를 매일 추적
- 수집 데이터를 Google Sheet에 누적하고, Google Chat으로 한국어 요약 알림 발송

---

## 2. 현재 실행 방식

로컬 Mac에서 수동 실행.

```bash
bash scripts/run_daily_local.sh
```

한 번 실행하면 아래를 순서대로 수행한다:

1. 광고 수집 (Meta Ad Library + Google Ads Transparency)
2. 방 개수 수집 (AirDNA, LiveAnywhere 웹, Encostay 웹)
3. 정책/공지 확인
4. Google Sheet 각 탭에 행 적재
5. Google Chat으로 한국어 요약 1건 발송

n8n, Cloud Run, GitHub Actions는 현재 보류.

---

## 3. Google Sheet 활성 탭

| 탭 | 내용 |
|---|---|
| `daily_ad_counts` | 경쟁사별 일일 광고 수 (Meta + Google) |
| `ad_creatives` | Meta 광고 소재 상세 (라이브러리 ID, 게재 시작일, 광고 문구 등) |
| `raw_supply_snapshots` | 공개방/공급량 수 (AirDNA, LiveAnywhere, Encostay) |
| `policy_updates` | 정책/공지 최신 글 및 변경 여부 |
| `run_log` | 실행 요약 로그 (시작/종료 시각, 실패 수, 상태) |

---

## 4. 광고 수집 규칙

### Meta Ad Library

- Broad keyword search 대신 고정 page_id URL 사용
- Page ID:
  - Airbnb: `324826532457`
  - LiveAnywhere: `352761898712591`
  - Encostay: `108631594606079`
- `meta_active_ads_count`: 광고주 페이지의 활성 광고 수
- `ad_creatives` 탭에 저장하는 필드:
  - `library_id`: 광고 카드에 표시된 "라이브러리 ID: NNNN" 에서 추출
  - `ad_detail_url`: `https://www.facebook.com/ads/library/?id=<library_id>`
  - `started_running_text`: 광고 시작일 원문 텍스트
  - `ad_start_date`: 파싱된 날짜 (YYYY-MM-DD)
  - `creative_text`: 광고 문구 (최대 500자)
  - `landing_url`: 랜딩 URL

### Google Ads Transparency

- Google Ads Transparency Center 사용
- 광고주명 (검색 및 클릭 대상):
  - Airbnb: `Airbnb, Inc.`
  - LiveAnywhere: `주식회사 리브애니웨어`
  - Encostay: `엔코위더스`
- `google_total_ads_count`: 광고주 상세 페이지 상단에 표시되는 광고 수 텍스트에서 파싱
- Google 광고 소재(creative)는 현재 수집하지 않음 (대용량 방지)

---

## 5. 방 개수 수집 규칙

### Airbnb (via AirDNA)

- Airbnb 사이트가 아닌 AirDNA에서 서울 Total Active Listings 수집
- 로그인 세션 필요: `.auth/airdna_state.json` (Playwright storage_state)
- 세션 발급: `python scripts/setup_airdna_session.py`
- 세션 없거나 만료 시: `status=login_required` 기록
- 세션 있어도 간헐적 `count_not_found` 발생 가능 → retry 로직 적용 (최대 ~20초)

### LiveAnywhere

- 국내 전체: 검색결과 N건 수집
- 서울: 검색결과 N건 수집

### Encostay

- `map-search` 페이지에서 `검색 결과 N개의 하우스` 수집
- 옵션 수는 제외하고 하우스 수만 저장
- 지도 viewport 기반 관측치로 정확한 전체 DB 수가 아님
- 동일한 URL + viewport (1920×1080) + zoom 조건을 유지해 일별 추이 비교용으로 사용
- `collection_method: map_view_estimate`

---

## 6. 정책/공지 수집 규칙

| 경쟁사 | URL |
|---|---|
| Airbnb | `https://news.airbnb.com/ko/category/public-policy/` |
| LiveAnywhere | `https://host.liveanywhere.me/notice/?category=` |
| Encostay | `https://host-support.enko.kr/hc/ko-kr/categories/14704971475727-%EA%B3%B5%EC%A7%80%EC%82%AC%ED%95%AD` |

- 목록 페이지 전체 텍스트가 아니라 최신 글 상세 페이지 내용을 중심으로 저장
- 이전 수집과 비교해 `is_new` (새 게시물) 또는 `is_changed` (제목 변경) 플래그 기록

---

## 7. 현재 알려진 이슈

| 항목 | 내용 |
|---|---|
| Meta 광고 소재 | `library_id`, `ad_detail_url`, `started_running_text`, `ad_start_date` 추출 계속 검증 중 |
| Google 광고 수 | 화면에 보이는 count와 수동 검증 필요 |
| AirDNA 안정성 | 로그인 세션 있어도 간헐적 `count_not_found` 발생 → 재시도/fallback 로직 추가됨 |
| Encostay 수 | map-view estimate로 정확한 전체 수가 아님. 추이 비교 전용 |
| 시트 헤더 | 코드 스키마의 영문 컬럼명과 일치해야 함. 한글 헤더로 변경 시 매핑 깨질 수 있음 |

---

## 8. 다음 할 일

1. `python -m src.jobs.collect_supply` — AirDNA 안정화 확인
2. `bash scripts/run_daily_local.sh` — 광고 + 방개수 + 정책 + 웹훅 전체 플로우 확인
3. Google Chat 요약에 `[공개방 수]` 섹션 포함 여부 확인
4. 데이터 품질 안정화 후 Looker Studio 대시보드 구축
