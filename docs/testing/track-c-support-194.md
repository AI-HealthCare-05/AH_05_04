# #194 지원 제안·Plan 생성 검증

- 날짜: 2026-09-15. Local 비식별 합성 검증, 배포/공개 승인 아님.
- 구현: 권가빈 @hazelnutflavoured. 책임 리뷰: @phina-io.
- 범위: [지원·생성 API](../contracts/proposed/track-c-support-plan-api-194.md) 두 route만.
- 환경: 전용 PostgreSQL 17/pgvector 컨테이너 `codex-194-api-test`, tmpfs `test` DB, loopback 15494.
  공유 개발 DB·기존 컨테이너는 사용하거나 변경하지 않는다.

## 자동 검증 범위

- 6개 Barrier의 실제 Rule·Copy와 첫 1개 지원, DECLINED 0개/NO_ELIGIBLE_SUPPORT.
- 후보 순서 역전·동률 code 정렬·후보 없음, 두 번째 eligible 지원 직접 선택 차단.
- 명시적 boolean true 확인, 누락·false·1·문자열 및 임의 config/약 ID 주입 거부.
- 최신 Check-in·Safety·Barrier 검증, non-ROUTINE 및 BLOCKED_ACTION 차단.
- 서버 부모 약 ID와 rule/copy/config snapshot의 실제 DB round-trip.
- 소유권 404, no-store, 인증, Idempotency-Key 필수, 최초 성공 replay 및 상이 payload 충돌.
- 같은 키·상이 키 동시 생성: 독립 DB 세션에서 최초 멱등 조회를 동기화해 경합을 강제한다.
- snapshot cap 초과 시 Plan·멱등 결과 동시 rollback 및 같은 키 재시도.
- 설정 장애는 503이며 정상 빈 제안으로 숨기지 않음. 원문 설정 오류 비노출.
- Safety 정정의 Plan 취소 뒤 생성 replay가 최초 ACTIVE 응답을 재현해도 DB는 CANCELLED 유지.
- OpenAPI 필수성·maxItems=1 및 제외한 완료/follow-up endpoint 부재.
- OpenAI Responses·Embeddings spy 0회. 이 서비스에는 Retriever/Generator 의존성이 없다.

## 실행 이력

초기 `94e5fa8d` 기반 Track C API 회귀: **60 passed**.
새 테스트의 짧은 멱등 키 3개를 기존 16자 이상 계약에 맞춰 수정한 뒤 재실행한 결과다.
전체 Backend·HandlerConfig·contract 회귀: **2,156 passed, 2 skipped** (242.31초).
Ruff check/format: PASS (921 files). Mypy: PASS (689 sources).
최신 #603 통합 후 결과는 아래에 추가한다.

```bash
DB_HOST=127.0.0.1 DB_PORT=15494 DB_EXPOSE_PORT=15494 \
DB_USER=synthetic DB_PASSWORD=synthetic DB_NAME=test PYTHONPATH=backend \
UV_PROJECT_ENVIRONMENT=/private/tmp/finalproject-issue-193/.venv UV_NO_SYNC=1 \
/private/tmp/finalproject-issue-193/.venv/bin/pytest \
  backend/app/tests tests/services/test_track_c_handler_config.py \
  tests/services/test_track_c_operational_config.py tests/contract -q
```

Frontend 인계 fixture: [합성 0/1개 제안·생성 요청·응답](../../tests/fixtures/post_mvp_1/track_c/support-plan-v1.json).
`tests/contract/test_track_c_support_fixture.py`가 DTO·운영 Rule/Copy 버전과 문구를 검증한다.

## 미실행·후속

Frontend 실제 클릭·일정 화면 연결, 배포 환경 검증, Plan 조회·완료·취소·follow-up API는 미실행/미구현이다.
Snapshot cap 회귀는 commit 전 전체 rollback 검증이며 외부 Provider live 호출을 수행하지 않는다.
DB schema/migration 변경은 없다. 이 부분 구현으로 #194를 닫지 않는다.
