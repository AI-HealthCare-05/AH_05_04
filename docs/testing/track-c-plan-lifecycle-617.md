# #617 Plan 조회·완료·취소 검증

- 구현: 권가빈. 단일 책임 리뷰어: 김지혜.
- 기준 develop: `1ebac025`. 변경 계약: [lifecycle v1](../contracts/current/track-c-plan-lifecycle-617.md).
- 테스트는 전용 PostgreSQL 17/pgvector 임시 DB와 합성 데이터만 사용한다.
- 구현 커밋: `405c0bbf`. 전용 DB는 loopback 15617/15618, Redis는 16618을 사용했다.
- 로컬 검증과 원격 CI 결과를 구분한다. 최종 책임 리뷰 승인·병합은 PR #618에서 확인한다.

## 집중 시나리오

- 기존 생성 응답과 snapshot 불변, 저장 상태 GET 및 no-store
- ACTIVE 단일 완료/취소, 종료 상태 변경·재활성화 거부
- strict confirmed=true, extra/missing field 거부, 인증·SELF 미존재/타인 동일 404
- 동일 key replay·다른 body 충돌, snapshot 저장 실패 시 상태·시각·멱등 기록 rollback
- Check-in/Safety 자동 취소 후 완료 거부, ROUTINE Safety/Barrier 정정 후 stale 완료 차단·사용자 취소 허용
- 서로 다른 DB session의 동시 완료/완료·완료/취소·동일 key, Check-in/Safety 정정 경합

## 실행 결과

| 검사 | 결과 |
| --- | --- |
| Track C API·HandlerConfig·운영 설정·계약 fixture 및 SMTP 재검증 | 146 passed |
| Backend·계약·서비스 회귀 최초 실행 | 2,299 passed, 2 skipped, SMTP 환경 의존 실패 3건 |
| SMTP 환경 수정 후 해당 파일 | 11 passed (146건에 포함) |
| Worker 설정 재검증 | 76 passed |
| 필수 스크립트 Backend lane | 2,568 passed, 128 skipped, SMTP 환경 실패 3건, 기존 Candidate Index teardown 오류 4건 |
| 필수 스크립트 Worker lane | 3,635 passed, Redis 기본값 환경 실패 1건 |
| 필수 스크립트 migration lane | 232 passed, 4 skipped; 단일 head `178c2d3e4f50` 및 schema 검증 통과 |
| Ruff check / format | PASS, 960 files |
| Mypy | PASS, 723 sources |
| DB logic / 보호 테이블 쓰기 경계 / test inventory | PASS |
| git diff --check / 변경 Markdown 로컬 참조 | PASS |

집중 실행 명령은 `pytest backend/app/tests/track_c tests/contract/test_track_c_support_fixture.py
 tests/services/test_track_c_handler_config.py tests/services/test_track_c_operational_config.py
 tests/services/test_email_delivery.py -q --tb=short`다. 실제 실행 시 한 줄로 연결한다.

### 전체 실행의 제한 및 기존 실패 재현

`ENV_FILE=<전용 합성 env> COMPOSE_FILE=<전용 tmpfs compose> bash scripts/ci/run_test.sh`를 실행했다.
최초 실행에서 로컬 `.env`의 `CHAT_HISTORY_CONTEXT_ENABLED=true`가 SMTP production-config 테스트를
먼저 차단했고, 테스트용 Redis 주소가 Worker의 승인 기본값 검사와 충돌했다.
제품 코드는 변경하지 않고 테스트 환경에 `CHAT_HISTORY_CONTEXT_ENABLED=false`, `REDIS_HOST=redis`,
`REDIS_PORT=6379`를 지정하자 해당 파일의 11건/76건은 통과했다. 실제 Redis 통합 연결은 runner가
전용 Compose의 host port로 주입한다.

Candidate Index 테스트 4건은 본문 PASS 뒤 teardown에서
`rag_candidate_index_build_test.rag_candidate_index_member` 미존재 오류가 발생했다.
변경 전 develop `1ebac025`를 별도 임시 checkout으로 추출한 후 다음 조합으로 **5 passed, 4 errors**를
재현했다. Track C 변경의 새 실패로 판단하지 않으며 본 PR에서 RAG 테스트 fixture를 수정하지 않는다.

```bash
pytest backend/app/tests/track_c/test_track_c_support_api.py::test_openapi_contains_only_scoped_routes_and_strict_confirmation \
  tests/integration/rag/test_candidate_index_build.py -q --tb=short
```

기존 Backend metadata fixture가 public 테이블을 만든 환경에서 Candidate Index의 schema 미지정
`create_all(checkfirst=True)`가 그 테이블을 발견하고, teardown은 전용 schema의 테이블을 TRUNCATE한다.
이 조합의 기존 fixture 정합화는 별도 범위다. 전체 스크립트는 exit 1로 종료했다.
Backend lane 실패로 후속 Redis 통합 테스트 및 coverage 합산/gate는 실행되지 않았다.
전체 스크립트 PASS·coverage gate 통과로 표시하지 않는다.

책임 리뷰 승인·Frontend 통합·실제 사용자 공개 완료를 의미하지 않는다.
Follow-up·실기기/브라우저 E2E·의료 규칙/Provider live 평가·배포는 이번 범위 밖이다.

## 원격 CI 및 문서 상태 리뷰

- 구현 HEAD `f8e4986f`의 [CI 실행](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/34991259236): test·lint 및 test-inventory·test-backend·test-rag·test-contract SUCCESS. frontend·test-migration·test-worker는 경로 분류로 SKIPPED.
- 위 원격 CI 결과는 앞서 기록한 로컬 통합 실행 실패나 미실행 coverage gate를 소급해 PASS로 바꾸지 않는다.
- [김지혜 책임 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/618#pullrequestreview-5216913531)의 계약 상태 정렬 요청을 반영했다. 현재 최종 승인·병합은 대기 중이다.
- 이번 수정은 문서만 변경한다. Markdown 렌더링·상대 링크·옛 경로 잔존·전체 diff 및 `git diff --check`를 확인하며, API 의미와 테스트 코드는 바꾸지 않아 Python 전체 회귀를 반복하지 않는다.

- 렌더링 확인은 로컬 Markdown→HTML 결과의 제목·표 구조와 텍스트를 대조했다. 브라우저 시각 확인은 file URL 접근 정책으로 실행하지 못했다.
