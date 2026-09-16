# #193 Local 7일 합성 데모 검증·실행

- 기준 develop: `a437a7d4`; 작업: `codex/193-196-internal-demo`.
- 구현 담당 권가빈 (@hazelnutflavoured), 책임 리뷰어 @phina-io.
- 상태: 구현·로컬 회귀 완료, 책임 리뷰 대기. 의료·Source·실사용 공개 승인 아님.
- 계약: [Safety API Proposed](../contracts/proposed/track-c-safety-barrier-api-193.md),
  [PD-193 revision 3](../governance/decisions/2026-09-16-track-c-internal-demo-193.md).

## 실행 범위

Backend API 데모다. 기존 Frontend는 증상 없음만 제출하며 증상 선택·안내 UI는 이번에 변경하지 않는다.
API body·response shape는 유지한다. 응답 message/copy/source 값에 대응하는 한국어 문구·선택 조건·
미검토 notice는 [versioned artifact](../../backend/app/config/track_c/safety-demo/track-c-safety-demo-2026-09-16.1.json)에 있다.

1. 실제 환자정보가 없는 격리 Local DB에 합성 SELF 계정·확정 처방·NOT_TAKEN Check-in을 준비한다.
2. `ENV=local`, `TRACK_C_SAFETY_DEMO_ENABLED=true` 및 합성 계정 UUID JSON 목록
   `TRACK_C_SAFETY_DEMO_USER_IDS=["<합성 계정 UUID>"]`를 서버 환경에 설정한다.
3. `TRACK_C_SAFETY_DEMO_STARTS_AT`, `TRACK_C_SAFETY_DEMO_EXPIRES_AT`를 timezone 포함 ISO 시각으로
   지정한다. 기간은 양수이고 최대 7일이다. 재시작으로 연장되지 않으며 시각을 자동 생성하지 않는다.
4. 합성 계정으로 인증한 후 기존 `POST /api/v1/safety-assessments`에 현재 Check-in ID/revision,
   symptom_codes, expected_revision 및 `Idempotency-Key`를 제출한다. 소유권·멱등 규칙은 기존과 같다.
5. `[]`는 ROUTINE, `SUDDEN_AIRWAY_SWELLING`은 EMERGENCY,
   `SUDDEN_OTHER_BODY_SWELLING`은 URGENT, `OTHER_SYMPTOM` 또는 미등록 구조화 코드는 UNKNOWN이다.
   코드만 보내면서 해당 선택 조건을 실제 진단 결과라고 표현하지 않는다.
6. 모든 non-ROUTINE에서 Barrier·일반 Support는 차단된다. 기존 ACTIVE Plan을 만든 뒤
   같은 Check-in에 expected_revision을 증가시켜 non-ROUTINE으로 정정하면 원자적으로 취소된다.
7. 시연 종료 시 flag를 끈다. 만료 시간 이후 flag가 남아도 새 demo mutation은 503으로 거부된다.
   이미 성공한 동일 멱등 요청의 소유자 replay는 최초 snapshot을 유지한다.

실제 설정이나 allowlist는 이번 작업에서 활성화하지 않았다. 공개 gate도 변경하지 않았다.

## 검증 범위

- `tests/services/test_track_c_demo_safety.py`: 11개 응급 코드, 긴급·기타·미등록, 혼합 우선순위·순서·중복,
  기본 OFF, 7일 상한·시작 포함/만료 제외, Local·allowlist, artifact 누락·변조, 조건 문구·Source 연결.
- `backend/app/tests/track_c/test_track_c_demo_safety_api.py`: 실제 HTTP·PostgreSQL 저장, Barrier 차단,
  Plan 취소·snapshot 실패 rollback, 만료 후 replay/신규 거부, 저장 0건, 소유권 오류, OpenAPI shape.
- 기존 Track C·설정 회귀와 저장소 필수 검사를 함께 실행한다.
- 외부 Provider를 호출하지 않는다. 위 결과는 임상 정확도 평가나 의료 승인 증빙이 아니다.
- [#196 확인 결과](track-c-rag-readiness-196.md)는 실제 RAG 연결 미구현과 필요한 인계를 기록한다.

## 결과

| 검사 | 최종 결과 |
| --- | --- |
| 신규 정책 단위 | 54 passed |
| 신규 실제 HTTP·PostgreSQL API | 14 passed |
| Migration | 237 passed, 4 skipped |
| Backend·Contract·PostgreSQL | 2,794 passed, 128 skipped |
| Redis 통합 | 29 passed |
| Worker core·OCR·RAG·Evaluation | 3,829 passed (환경 수정 후 전체 lane 재실행) |
| 전체 Ruff / format | PASS / 1,005 files formatted |
| Mypy Backend·Worker | PASS / 759 source files |
| Backend·Redis + 재실행 Worker 합산 coverage | 92%, threshold PASS |
| diff / 변경 Markdown 상대 링크 | `git diff --check` PASS / 누락 0개 |

`scripts/ci/run_test.sh` 최초 실행은 임시 테스트 환경의 `REDIS_HOST=127.0.0.1` 때문에
Worker의 승인 기본값(`redis`) 검사 1건이 실패해 exit 1이었다. 이때 migration·Backend·Contract·
PostgreSQL·Redis lane은 위 수치로 모두 통과했다. 저장소 코드 변경 없이 별도 합성 환경 파일을
승인 기본값으로 바로잡고 같은 Worker 명령(`pytest -n 2 --dist=loadfile --max-worker-restart=0
--cov --cov-report= ai_worker/tests/core ai_worker/tests/ocr ai_worker/tests/rag ai_worker/tests/evaluation`)을
전량 재실행했다. 동일 코드의 완료된 Backend·Redis coverage를 보존해 재실행 Worker coverage와
합산했고 coverage threshold도 통과했다. 최초 전체 스크립트 자체가 exit 0이었다고 기록하지 않는다.

실행은 이번 작업 전용 PostgreSQL·Redis 컨테이너와 합성 credential만 사용했다. 기존 개발 DB·다른
작업 컨테이너를 사용하거나 재생성하지 않았다. 로그·coverage는 로컬 검증용이며 저장소에 커밋하지 않는다.

실사용/공개 배포, 실제 환자 입력, 외부 Provider, Frontend 브라우저 검증은 실행하지 않았다.
이번 범위는 Backend 결정론적 합성 정책이며 RAG·LLM/의료 정확도 평가를 대신하지 않는다.
책임 리뷰·CI·의료/Source 공개 승인과 실제 데모 활성화는 별도다.
