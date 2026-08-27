# 교차 계층 테스트

개별 모듈 내부 테스트가 아니라 서비스 경계를 검증하는 영역입니다. 디렉터리의 존재와 실제 자동 테스트 구현·CI 연결 상태를 구분합니다.

- `contract/`: 현재 Backend–AI Core 계약 테스트가 구현되어 기본 CI에서 실행됨
- `integration/`: 공통 CORS·오류 테스트가 구현되어 있으나 기본 CI에는 연결되지 않음
- `e2e/`: 전체 MVP 사용자 여정 테스트를 위한 준비 영역이며 현재 테스트 없음
- `fixtures/`: 개인정보가 없는 합성 입력과 기대 결과

Redis Stream과 실제 Worker 통합 테스트는 Post-MVP 후속 범위입니다. 현재 기본 CI에는 Backend 테스트, Backend–AI Core 계약 테스트와 `ai_worker/tests/core/`의 Worker 공통 단위 테스트가 포함됩니다. Backend 내부의 PostgreSQL·채팅 동시성 통합 테스트는 `backend/app/tests/chat_integration/`에 있으며 기본 `backend/app` 테스트 범위에 포함됩니다.

실제 의료문서나 환자 데이터는 fixture로 사용하지 않습니다.
