# 데이터 스키마 — competitive-growth-monitor

**Competitor Action Monitoring** 시스템의 데이터 사전입니다.

모든 데이터는 Google Sheets에 저장됩니다. 각 탭의 컬럼 순서는 `config/sheet_schema.yaml`에 정의된 순서와 일치합니다. 행은 append-only이며 수정 또는 삭제하지 않습니다.

---

## 탭: `raw_supply_snapshots`

Job: `src/jobs/collect_supply.py`  
수집 주기: 일 1회  
설명: 경쟁사별·시장별 공개 검색 결과 기준 매물 수 스냅샷

| 컬럼 | 타입 | 예시 | 설명 |
|------|------|------|------|
| `collected_at` | datetime (UTC) | `2026-05-08T06:00:12Z` | 수집 시각 (ISO-8601) |
| `env` | string | `prod` | 실행 환경 (`dev` / `staging` / `prod`) |
| `competitor` | string | `airbnb` | `competitors.yaml` 기준 경쟁사 키 |
| `market` | string | `Seoul` | 시장 레이블 |
| `listing_count` | integer | `1842` | 수집된 활성 매물 수; 수집 실패 시 null |
| `source_url` | string | `https://…` | 파싱에 사용된 공개 URL |

**주요 활용:** 경쟁사별·시장별 매물 수 트렌드 시각화. 광고 활동 또는 정책 변경과 함께 분석하면 경쟁사의 공급 확장·축소 의도를 추론할 수 있습니다.

---

## 탭: `ad_activity_snapshots`

Job: `src/jobs/collect_ads.py`  
수집 주기: 일 1회  
설명: 경쟁사 광고 활동 스냅샷. MVP 수집 소스는 마케팅 팀이 현재 수동으로 사용 중인 두 가지 공개 광고 라이브러리입니다. 광고 지출 금액은 두 소스 모두 공개하지 않으므로 수집하지 않습니다.

**수집 소스 (MVP):**
- Meta Ad Library: https://www.facebook.com/ads/library/
- Google Ads Transparency Center: https://adstransparency.google.com/?region=KR

| 컬럼 | 타입 | 예시 | 설명 |
|------|------|------|------|
| `observed_at` | datetime (UTC) | `2026-05-08T07:05:44Z` | 행 기록 시각 |
| `date` | date | `2026-05-08` | 광고 데이터의 리포팅 기준일 (export 리포트의 경우 observed_at과 다를 수 있음) |
| `competitor` | string | `airbnb` | 경쟁사 키 |
| `source_platform` | string | `meta` | 광고가 게재되는 플랫폼 (`meta` / `google` / `other`) |
| `source_tool` | string | `meta_ad_library` | 데이터 수집에 사용한 도구 (`meta_ad_library` / `google_ads_transparency` / `manual` / `other`) |
| `advertiser_name` | string | `Airbnb` | 광고 라이브러리에 표시된 광고주명 |
| `ad_id` | string | `12345678` | 플랫폼 광고 ID; 미제공 시 빈 값 |
| `ad_status` | string | `active` | 소스 기준 광고 상태 (`active` / `inactive` / `unknown`) |
| `ad_start_date` | date | `2026-04-20` | 광고 시작일; 미공개 시 빈 값 |
| `ad_end_date` | date | `2026-05-01` | 광고 종료일; 집행 중이거나 미공개 시 빈 값 |
| `creative_text` | string | `Find the perfect…` | 광고 헤드라인 및/또는 본문 (최대 500자) |
| `creative_format` | string | `image` | 크리에이티브 형식 (`image` / `video` / `carousel` / `text` / `unknown`) |
| `landing_url` | string | `https://…` | 광고 연결 랜딩 URL; 미공개 시 빈 값 |
| `active_ad_count` | integer | `14` | 관측 시점 기준 해당 경쟁사·플랫폼의 활성 광고 수; 해당 없으면 null |
| `new_ad_count` | integer | `3` | 직전 관측 이후 새로 시작된 광고 수; 계산 불가 시 null |
| `estimated_spend` | number | `1500000` | 허용된 export 또는 공식 API가 명시적으로 제공하는 경우의 추정 지출; **Meta Ad Library·Google Ads Transparency Center에서는 추정하지 않음**; 미제공 시 null |
| `spend_currency` | string | `KRW` | 지출 통화 코드; `estimated_spend`가 null이면 빈 값 |
| `confidence` | string | `medium` | 데이터 신뢰도 (`high` = 공식 API, `medium` = 공개 라이브러리, `low` = 수동 입력) |
| `source_url` | string | `https://…` | 수집에 사용한 광고 라이브러리 페이지 URL 또는 파일 참조 |
| `status` | string | `ok` | 수집 상태 (`ok` / `partial` / `failed`) |
| `error_message` | string | `timeout` | `status`가 `partial` 또는 `failed`인 경우의 오류 메시지; 정상 시 빈 값 |

**주요 활용:** 활성 광고 수 추이, 신규 캠페인 감지, 광고 소재 및 랜딩 URL 변화, 계절성 광고 파악. 매물 수 변화 또는 정책 변경과 함께 분석하면 경쟁사의 캠페인 의도를 추론할 수 있습니다.  
**주의:** `estimated_spend`는 항상 null입니다. Meta Ad Library와 Google Ads Transparency Center는 광고 지출 금액을 공개하지 않습니다.

---

## 탭: `policy_change_log`

Job: `src/jobs/detect_policy_changes.py`  
수집 주기: 6시간마다  
설명: 경쟁사 정책 페이지 텍스트 변경 이력. 변경이 없으면 행을 추가하지 않습니다.

| 컬럼 | 타입 | 예시 | 설명 |
|------|------|------|------|
| `detected_at` | datetime (UTC) | `2026-05-08T12:01:33Z` | 변경 감지 시각 |
| `env` | string | `prod` | 실행 환경 |
| `competitor` | string | `airbnb` | 경쟁사 키 |
| `policy_page` | string | `cancellation_policy` | `policy_pages.yaml` 기준 페이지 키 |
| `url` | string | `https://…` | 모니터링 대상 URL |
| `previous_hash` | string | `a3f2c1…` | 이전 페이지 텍스트의 SHA-256; 최초 스냅샷 시 빈 값 |
| `current_hash` | string | `9e1b7d…` | 현재 페이지 텍스트의 SHA-256 |
| `diff_summary` | string | `Content changed…` | 변경 내용 요약 발췌 (최대 300자) |
| `alert_sent` | boolean | `TRUE` | Google Chat 알림 발송 여부 |

**주요 활용:** 수수료 정책·취소 정책·호스트 약관·프로모션 조건 변경 감사 로그. **MVP 핵심 시그널.** 광고·공급 시그널과 결합하면 경쟁사의 전략적 의도(공급자 확보, 리포지셔닝, 신규 오퍼 런칭 등)를 추론할 수 있습니다.

---

## 탭: `viral_reputation_mentions`

Job: `src/jobs/collect_reputation.py`  
수집 주기: 일 1회  
설명: SNS, 뉴스, 커뮤니티에서 수집된 경쟁사 관련 바이럴 언급

| 컬럼 | 타입 | 예시 | 설명 |
|------|------|------|------|
| `collected_at` | datetime (UTC) | `2026-05-08T08:10:00Z` | 수집 시각 |
| `env` | string | `prod` | 실행 환경 |
| `competitor` | string | `liveanywhere` | 경쟁사 키 |
| `platform` | string | `reddit` | 출처 플랫폼 (`twitter` / `reddit` / `youtube` / `news` / `blog` / `other`) |
| `mention_url` | string | `https://…` | 원본 게시물 또는 기사 URL |
| `title` | string | `Is LiveAnywhere…` | 제목 또는 헤드라인 (최대 255자) |
| `sentiment` | string | `negative` | 감성 레이블 (`positive` / `negative` / `neutral` / `unknown`) |
| `snippet` | string | `Users report…` | 관련 발췌 (최대 500자) |
| `engagement_score` | integer | `342` | 참여도 지표 (좋아요+공유+댓글 합산); 미제공 시 null |
| `source_url` | string | `https://…` | 언급 발견에 사용된 검색 또는 피드 URL |

**주요 활용:** 부정 이슈 조기 감지, 바이럴 마케팅 캠페인 모니터링

---

## 탭: `alert_log`

기록 주체: `src/integrations/google_chat.py` 및 알림을 발송하는 모든 Job  
설명: 발송된 Google Chat 알림 전체 이력. 팀의 후속 조치 확인에 활용됩니다.

| 컬럼 | 타입 | 예시 | 설명 |
|------|------|------|------|
| `sent_at` | datetime (UTC) | `2026-05-08T12:01:40Z` | 알림 발송 시각 |
| `env` | string | `prod` | 실행 환경 |
| `alert_type` | string | `policy_change` | 알림 유형 (`policy_change` / `supply_threshold` / `reputation_spike` / `job_failure`) |
| `competitor` | string | `encostay` | 해당 경쟁사; Job 수준 알림이면 빈 값 |
| `message` | string | `정책 페이지 변경…` | 알림 메시지 본문 (최대 500자) |
| `channel` | string | `competitive-intel` | 알림을 수신한 Google Chat 스페이스 또는 Webhook 식별자 |
| `triggered_by_job` | string | `detect_policy_changes` | 알림을 발생시킨 Job 이름 |
| `acknowledged` | boolean | `FALSE` | 팀이 확인 완료 시 수동으로 TRUE로 변경 |

**주요 활용:** 알림 발송 이력 감사, 팀 대응 여부 추적

---

## 공통 규칙

- 모든 타임스탬프는 UTC ISO-8601 형식입니다.
- `env` 컬럼으로 개발/운영 환경을 구분합니다. Looker Studio에서 필터로 활용하세요.
- 행은 append-only입니다. 잘못된 데이터는 삭제 대신 보정 행을 추가합니다.
- `listing_count`가 null인 경우 수집 실패를 의미합니다. 값을 보간하지 마세요.
- `source_tool = manual`인 행은 자동화 수집이 아닌 팀이 직접 입력한 데이터입니다.

---

## 복합 액션 시그널 해석 가이드

각 탭은 독립적으로도 유용하지만, **여러 탭의 데이터를 동일한 경쟁사·동일한 시간대에서 교차 분석할 때** 경쟁사의 전략적 의도를 추론할 수 있습니다.

| 관찰 (탭 + 조건) | 추정 경쟁사 행동 |
|---|---|
| `ad_activity_snapshots.active_ad_count` 증가 + `raw_supply_snapshots.listing_count` 증가 (동일 시장) | 수요·공급 확장 캠페인 가능성 |
| `policy_change_log` 호스트 약관 변경 + `raw_supply_snapshots.listing_count` 증가 | 공급자 확보(acquisition) 드라이브 가능성 |
| `ad_activity_snapshots.landing_url` 신규 등장 + `policy_change_log` 프로모션 페이지 변경 | 신규 캠페인 또는 오퍼 런칭 가능성 |
| `ad_activity_snapshots.creative_text` 변경 + `policy_change_log` 정책 변경 | 리포지셔닝 또는 가격 전략 변화 가능성 |

이 해석은 대시보드의 "복합 액션 시그널" 섹션에서 팀이 수동으로 수행합니다. 자동 판정하지 않습니다.
