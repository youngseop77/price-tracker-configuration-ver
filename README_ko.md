# 네이버 쇼핑 최저가 / 셀러 자동 추적기 (Advanced)

이 프로젝트는 네이버 쇼핑에서 특정 상품의 최저가와 판매 셀러를 자동으로 추적하고, 가격 변동 및 하락 알림을 제공합니다.

## ✨ 주요 기능

1. **가격 변동 정밀 감지**: 직전 성공 수집값과 비교하여 `PRICE_DOWN`, `PRICE_UP`, `PRICE_SAME` 상태를 기록합니다.
2. **API → 브라우저 자동 폴백**: API 검색 결과가 없을(`NO_MATCH`) 경우 자동으로 Playwright 브라우저를 구동하여 수집을 보완합니다.
3. **가격 하락 알림**: 직전 가격 대비 설정된 임계값(기본 5%) 이상 하락 시 `price_alerts.log`에 기록하고 경고를 출력합니다.
4. **고도화된 리포트**: `export-html` 명령으로 가격 변동 이력이 컬러로 시각화된 HTML 리포트를 생성합니다.
5. **안전한 데몬 운영**: `asyncio.sleep`과 `time.monotonic`을 사용하여 이벤트 루프 블로킹 없이 안정적인 주행을 지원합니다.
6. **강력한 설정 검증**: 실행 전 `targets.yaml`의 모든 설정 오류를 전수 조사하여 즉시 리포트합니다 (Fail-Fast).
7. **DB 자동 마이그레이션**: 컬럼이 추가되어도 기존 DB 손실 없이 자동으로 스키마를 갱신합니다.

## 🚀 빠른 시작

### 설치 및 설정
```bash
# 가상환경 생성 및 설치
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 설정 파일 생성
cp .env.example .env
cp targets.example.yaml targets.yaml
```

### 실행 및 리포트
```bash
# 1회 즉시 실행
PYTHONPATH=./src python -m tracker.main once

# HTML 리포트 생성
PYTHONPATH=./src python -m tracker.main export-html --html-out report.html

# 데몬 모드 (30분 간격)
PYTHONPATH=./src python -m tracker.main daemon --interval 1800
```

## ⚙️ 설정 가이드

### targets.yaml 명세
- `fallback_url`: `api_query` 모드 전용. API 결과가 없을 때 이동할 URL.
- `alert_threshold_percent`: (common 섹션) 알림 기준 하락폭 (예: 5.0).
- `required_keywords` & `exclude_keywords`: 검색 결과 중 정확히 원하는 상품만 골라내기 위한 필터링 옵션.
- `product_id`: 네이버 쇼핑 상품 ID. 정확한 매칭을 위해 입력을 강력 권장합니다.

### 📊 데이터 필드 정의
- `config_mode`: `targets.yaml`에 정의된 원래 수집 모드 (`api_query` 또는 `browser_url`)
- `source_mode`: 실제로 수집에 성공(또는 시도)한 모드. 폴백 시 `api_query` -> `browser_url`로 변할 수 있습니다.
- `fallback_used`: API 검색 실패 후 브라우저 폴백으로 성공한 경우 `1`, 그 외 `0`.
- `status`: 수집 상태 (`OK`, `NO_MATCH`, `BrowserScrapeError` 등). 순수 상태값만 유지됩니다.

### 가격 변동 상태
- `FIRST_SEEN`: 첫 수집됨
- `PRICE_SAME`: 가격 변동 없음
- `PRICE_DOWN`: 가격 하락 (초록색 표시)
- `PRICE_UP`: 가격 상승 (빨간색 표시)

### 💡 정확한 추적을 위한 팁
1. **정합성 향상**: 액세서리(케이스, 필름 등)가 최저가로 잡히는 것을 방지하려면 `exclude_keywords`에 아래 단어들을 추가하세요.
   - 키워드 예: `케이스`, `커버`, `필름`, `파우치`, `이어팁`, `호환`, `교체`, `부품`, `스트랩`, `거치대`, `스탠드`, `충전기`, `케이블`
2. **고객 고유 식별**: 가능하다면 `required_keywords`에 모델명(예: `MXH02FE/A`)을 포함하거나, `match` 섹션에 `product_id`를 직접 지정하는 것이 가장 정확합니다.
3. **리포트 수집경로 해석**:
   - `api_query → api_query`: API로 정상 수집
   - `api_query → browser_url [FALLBACK]`: API 검색 실패 후 브라우저 폴백 성공
   - `browser_url → browser_url`: 처음부터 브라우저로 수집

## 📁 파일 구조
- `price_tracker.sqlite3`: 모든 관측 데이터 저장
- `price_alerts.log`: 가격 하락 알림 기록 전용 로그
- `artifacts/`: 브라우저 모드 실패 시 스크린샷 및 HTML 저장
