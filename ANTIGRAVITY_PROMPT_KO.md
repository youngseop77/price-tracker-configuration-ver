이 프로젝트는 네이버 쇼핑 최저가/셀러 추적기입니다.

목표:
1. targets.yaml 에 등록된 상품을 30분마다 조회한다.
2. 가장 저렴한 가격과 seller 이름을 SQLite에 기록한다.
3. API로 정확히 찾지 못하는 상품은 browser_url 모드(Playwright)로 보완한다.
4. browser_url 모드는 실패 시 HTML과 스크린샷을 artifacts/ 에 저장한다.

Antigravity에서 해야 할 작업:
1. .env.example 을 .env 로 복사하고 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 설정
2. targets.example.yaml 을 targets.yaml 로 복사 후 우리 상품명/키워드/URL로 수정
3. 가상환경 생성 및 dependencies 설치
4. Playwright Chromium 설치: `playwright install chromium`
5. `python -m tracker.main once --config ./targets.yaml --db ./price_tracker.sqlite3 --verbose` 실행
6. browser_url 모드 실패 시 artifacts/html, artifacts/screenshots 를 보고 selector 조정
7. 안정화 후 `python -m tracker.main daemon --interval 1800` 또는 systemd/cron 등록

코드 수정 원칙:
- API 모드를 우선 사용하고, browser 모드는 예외 케이스에만 사용
- 실패 기록도 DB에 남기기
- 페이지 구조 변경에 대비해 selector를 config로 분리하기
- price/seller 파싱 로직은 함수 단위로 작게 유지하기
