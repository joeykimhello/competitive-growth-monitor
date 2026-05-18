# competitive-growth-monitor

경쟁사의 광고 활동, 정책·공지 변경을 추적하여 마케팅·공급 전략 수립을 지원하는 내부 도구입니다.  
대상 경쟁사: **Airbnb**, **리브애니웨어 (LiveAnywhere)**, **엔코스테이 (Encostay)**

> **중요:** 33m2는 자사 서비스입니다. 경쟁사 분석에서 제외되며 어떤 설정 파일에도 포함되지 않습니다.

---

## MVP 워크플로우 (3개)

### 1. Meta Ad Library
- URL: https://www.facebook.com/ads/library/
- 각 경쟁사(Airbnb, 리브애니웨어, 엔코스테이)의 활성 광고 수 + 광고 소재 텍스트 + 랜딩 URL 수집
- UI/내비게이션/푸터 텍스트는 광고 소재에서 제외
- 활성 광고 수와 소재는 별도 탭에 저장 (active_ad_count는 daily_ad_counts, 소재는 ad_creatives)

### 2. Google Ads Transparency Center
- URL: https://adstransparency.google.com/?region=KR
- 각 경쟁사를 검색·선택하여 광고주 갤러리 페이지 도달
- 플랫폼별 광고 수 수집: Google Maps, Google Play, Google Shopping, Google Search, YouTube
- 경쟁사당 하루 1개 집계 행 저장
- 광고주를 찾을 수 없는 경우 실패 구조체 저장

### 3. 정책/공지 업데이트
- Airbnb 공개 정책: https://news.airbnb.com/ko/category/public-policy/
- 리브애니웨어 호스트 공지: https://host.liveanywhere.me/notice/?category=
- 엔코스테이 공지사항: https://host-support.enko.kr/hc/ko-kr/categories/14704971475727-%EA%B3%B5%EC%A7%80%EC%82%AC%ED%95%AD
- 최신 게시물 제목·URL·게재일 추적
- 새 게시물 여부(is_new) 및 내용 변경 여부(is_changed) 감지
- 한국어 요약 생성
- 리브애니웨어는 이미지 게시물 가능성 있음 → image_url 저장, status=image_needs_review

---

## 데이터 흐름

```
[로컬 Mac]  scripts/run_daily_local.sh  (수동 또는 cron)
    │
    └─ python -m src.jobs.run_daily
           │
           ├─ [1] collect_ads.py
           │       ├─ Meta Ad Library    → daily_ad_counts (meta 컬럼) + ad_creatives
           │       └─ Google Ads TC      → daily_ad_counts (google 컬럼)
           │
           ├─ [2] detect_policy_changes.py
           │       └─ 공지사항 3곳       → policy_updates
           │
           ├─ run_log 1개 행 기록
           └─ Google Chat 한국어 요약 발송 (1회, 모든 워크플로우 완료 후)
    │
    ▼
[Google Sheets]
    ├─ daily_ad_counts    — 경쟁사별 일일 광고 수
    ├─ ad_creatives       — Meta 광고 소재
    ├─ policy_updates     — 공지사항 변경 이력
    └─ run_log            — 일일 실행 로그
```

---

## Google Sheets 탭 구조

### 활성 탭 (현재 데이터 기록)

| 탭 | 기록 주체 | 설명 |
|----|----------|------|
| `daily_ad_counts` | `collect_ads.py` | 경쟁사별 Meta/Google 광고 수 (하루 1행/경쟁사) |
| `ad_creatives` | `collect_ads.py` | Meta 광고 소재 텍스트·랜딩 URL |
| `policy_updates` | `detect_policy_changes.py` | 공지사항 확인 결과 및 변경 감지 |
| `run_log` | `run_daily.py` | 전체 실행 결과 요약 |

### 레거시 탭 (더 이상 기록 안 함 — 과거 데이터 보존)

`raw_supply_snapshots`, `ad_activity_snapshots`, `policy_change_log`, `viral_reputation_mentions`, `alert_log`

컬럼 상세: [`config/sheet_schema.yaml`](config/sheet_schema.yaml)

---

## Google Sheets 준비

신규 스프레드시트를 사용하는 경우 아래 탭을 수동으로 생성하세요 (탭 이름 정확히 일치해야 함):
- `daily_ad_counts`
- `ad_creatives`
- `policy_updates`
- `run_log`

기존 스프레드시트를 업그레이드하는 경우 위 탭을 추가하고 기존 탭은 그대로 두면 됩니다.

---

## 실행 방법

### 일일 실행 (권장)

```bash
bash scripts/run_daily_local.sh
```

세 개 워크플로우(Meta 광고, Google 광고, 정책 공지)를 순차 실행하고 완료 후 Google Chat에 한국어 요약을 발송합니다. exit 0이면 전체 성공(또는 partial), exit 1이면 과반 실패.

### 개별 실행 (디버깅)

```bash
source .venv/bin/activate

# 광고 수집만 실행 (Meta + Google)
python -m src.jobs.collect_ads

# 정책/공지 확인만 실행
python -m src.jobs.detect_policy_changes

# 전체 오케스트레이터 (위 둘 + run_log + Google Chat 요약)
python -m src.jobs.run_daily

# Google Chat webhook 연결 테스트
python -m src.jobs.test_google_chat_alert

# Google Sheets append 테스트
python -m src.jobs.test_google_sheets_append
```

### macOS 자동 실행 (선택)

**cron** (`crontab -e`):
```cron
# 매일 오전 9시 KST (00:00 UTC) 실행
0 0 * * * cd /path/to/competitive-growth-monitor && bash scripts/run_daily_local.sh >> ~/cgm-daily.log 2>&1
```

---

## 프로젝트 구조

```
competitive-growth-monitor/
├── config/
│   ├── competitors.yaml        # 경쟁사 정의 (광고 라이브러리 URL, 광고주명 등)
│   ├── sheet_schema.yaml       # Google Sheets 탭·컬럼 정의 (활성 4개 + 레거시)
│   ├── policy_pages.yaml       # 모니터링 대상 공지사항 페이지 URL
│   ├── supply_sources.yaml     # 매물 수 수집 설정 (현재 미사용)
│   └── ad_sources.yaml         # 광고 수집 소스 설정 (참고용)
├── scripts/
│   └── run_daily_local.sh      # 로컬 일일 실행 스크립트
├── src/
│   ├── collectors/
│   │   └── ads/
│   │       ├── meta_ad_library.py         # Meta Ad Library Playwright 수집기
│   │       └── google_ads_transparency.py # Google Ads TC Playwright 수집기 (플랫폼별 카운트)
│   ├── integrations/
│   │   ├── google_sheets.py    # Sheets API 클라이언트
│   │   └── google_chat.py      # Google Chat Webhook 클라이언트
│   └── jobs/
│       ├── run_daily.py             # [메인] 전체 오케스트레이터 + run_log + Korean Chat 요약
│       ├── collect_ads.py           # Meta + Google 광고 수집 → daily_ad_counts + ad_creatives
│       ├── detect_policy_changes.py # 공지사항 확인 → policy_updates
│       └── collect_supply.py        # [보류] 매물 수 수집
├── data/
│   └── snapshots/
│       ├── ads/     # HTML 스냅샷 (디버깅용)
│       └── policy/  # 공지사항 최신 상태 JSON 캐시 (변경 감지용)
├── .env.example
├── requirements.txt
└── README.md
```

---

## 환경 변수

`.env.example` 참고. `.env`와 서비스 계정 JSON은 절대 커밋하지 않습니다.

| 변수 | 필수 | 용도 |
|------|------|------|
| `GOOGLE_APPLICATION_CREDENTIALS` | **예** | Google 서비스 계정 JSON 경로 |
| `GOOGLE_SHEET_ID` | **예** | 대상 스프레드시트 ID |
| `GOOGLE_CHAT_WEBHOOK_URL` | **예** | Google Chat Incoming Webhook URL |
| `ENV` | 아니오 | `dev` 또는 `prod` (기본값 `dev`) |

---

## 로컬 설정

```bash
# 1. 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate

# 2. 의존성 설치
pip install -r requirements.txt

# 3. Playwright 브라우저 설치 (광고 수집에 필요)
playwright install chromium

# 4. 환경 변수 설정
cp .env.example .env
# .env 파일에 GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_SHEET_ID, GOOGLE_CHAT_WEBHOOK_URL 입력

# 5. Google 서비스 계정 JSON을 GOOGLE_APPLICATION_CREDENTIALS 경로에 배치
```

---

## 광고 데이터 소스

| 소스 | URL | 로그인 필요 |
|------|-----|------------|
| Meta Ad Library | https://www.facebook.com/ads/library/ | 아니오 |
| Google Ads Transparency Center | https://adstransparency.google.com/?region=KR | 아니오 |

광고 지출 금액은 두 소스 모두 공개하지 않으므로 수집하지 않습니다.

### Google Ads Transparency Center 수집 방식

URL 파라미터로 광고주를 직접 지정할 수 없으므로 Playwright로 아래 흐름을 자동화합니다:
1. 시작 페이지 로드
2. 검색창에 광고주명 입력
3. 자동완성 드롭다운에서 첫 번째 항목 선택
4. 복수 광고주 disambiguation 패널이 나타나면 첫 번째 광고주 클릭
5. 광고주 갤러리 페이지(`/advertiser/{ID}?region=KR`) 도달
6. 플랫폼별 광고 수 추출

---

## 추후 도입 (미정)

- n8n Cloud + Cloud Run 기반 팀 공유 스케줄링
- 대시보드 (Looker Studio)
- 공급 수집 워크플로우 재활성화
