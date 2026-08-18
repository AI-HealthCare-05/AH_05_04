# 교차 계층 테스트

개별 모듈 내부 테스트가 아니라 서비스 경계를 검증합니다.

- `contract/`: OpenAPI, DTO와 AI Worker 메시지 스키마
- `integration/`: FastAPI, MySQL, Redis Stream과 AI Worker 연동
- `e2e/`: MVP 핵심 사용자 여정
- `fixtures/`: 개인정보가 없는 합성 입력과 기대 결과

실제 의료문서나 환자 데이터는 fixture로 사용하지 않습니다.
