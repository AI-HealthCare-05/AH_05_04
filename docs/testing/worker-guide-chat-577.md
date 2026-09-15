# #577 1차 Guide·Chat 합성 조립 검증

- 기준: develop `122be114`, 2026-09-15 (정현우 리뷰 반영 후 재검증)
- 상태: 1차 합성 조립 검증 완료. #577 전체 완료 또는 #180 Runtime 완료가 아님.
- 범위 확인: 2026-09-15 13:34 정현우 답변에 따른 기존 공통 계약 기반 내부 조립.

## 구현 범위

1. 기존 Handler·HandlerSuccess·ResultStore 계약으로 Guide·Chat factory 주입점을 제공한다.
2. OCR Provider 유무와 독립적으로 등록한다. 혼합 등록 시 OCR 전용 시작 처리는 OCR에만 적용한다.
3. Job 종류별 ResultStore로 분기하고 Handler/저장소 누락과 Handler 종류 불일치를 차단한다.
4. 실제 assembly와 Consumer를 합성 Handler·Fake/Spy 저장소로 연결해 실행, 저장, commit, ACK 순서를 검증한다.
5. delivery의 `job_type`에 해당하는 factory만 조립해 종류 간 영향을 격리한다.
6. `async-job-v1`의 종류별 실행 상한(OCR·GUIDE 60초/75초, CHAT 45초/60초)을 Consumer 실행에 적용한다.

Factory는 delivery의 동일 session에서 Handler와 저장소를 조립한다. 저장소는 자체 commit을
하지 않고 현재 transaction에 저장하며, Consumer의 기존 commit-before-ACK 경계를 사용한다.
payload·권한·도메인 무결성은 인계될 Application Service/Repository에서 관리한다.
DB schema, RLS, DB Trigger, Stored Procedure는 추가하지 않았다.

등록 대상 Handler의 정본은 `ContextAwareHandler`다. runtime이 모든 실행에 `context=`를 전달하므로
`handle(message)`만 받는 구현체는 정적 검사에서 걸러지고, 구현체는 `context is None`을
`INTERNAL_ERROR`로 닫는다. 조립 실패(예외·종류 불일치·누락)는 해당 종류를 등록하지 않는 것으로
끝내고 예외를 delivery 밖으로 올리지 않는다. 등록되지 않은 job_type은 Dispatcher가 기존 실패
정책대로 처리하므로 attempt·실패 기록·ACK 경계가 유지된다.

## 정현우 리뷰 반영 (2026-09-15)

| 지적 | 반영 |
| --- | --- |
| [BLOCKER] CHAT timeout·lease가 승인 계약과 다름 | `WORKER_CHAT_HARD_TIMEOUT_SECONDS`(45초)·`WORKER_CHAT_LEASE_DURATION_SECONDS`(60초)를 분리하고 delivery의 `job_type`으로 선택. GUIDE 60/75·CHAT 45/60·OCR 60/75를 고정하는 테스트 추가 |
| [MUST FIX] 조립 실패가 Job 실패 경계를 우회하고 다른 종류까지 막음 | 해당 delivery의 factory만 조립하고, 실패 시 예외 대신 미등록으로 처리해 기존 실패 기록·ACK 경계를 사용. public `execute()` 기준 실패 기록·ACK와 타 종류 격리를 검증 |
| [MUST FIX] Factory 타입과 실제 Handler 호출 계약이 갈라짐 | factory 타입을 `ContextAwareHandler`로 통일하고 Registry 등록이 가능하도록 `context` 기본값을 정렬. README·이 문서의 계약 설명을 함께 갱신 |

## 검증 결과

- `pytest ai_worker/tests/core/test_guide_chat_assembly.py -q`: 26개 통과.
- `pytest ai_worker/tests/core ai_worker/tests/ocr -q`: 기존 OCR 관련 검증을 포함해 475개 통과.
- 종류별 실행 상한(OCR 60/75, GUIDE 60/75, CHAT 45/60)을 Consumer 실행 값으로 고정 검증했다.
- factory 예외·잘못된 종류·누락에서 해당 종류만 미등록되고 다른 종류와 OCR은 정상 조립됨을 확인했다.
- public `execute()` 경로에서 조립 실패가 `INTERNAL_ERROR` 실패 기록과 ACK로 끝나는 것을 확인했다.
- Guide/Chat 각각 OCR 유무에 따른 실행·저장·commit·ACK 순서를 확인했다.
- 저장 실패 및 결과 commit 실패 시 rollback, 미저장, ACK 금지를 확인했다.
- Handler/저장소 누락, 종류 불일치, delivery별 생성, 외부 factory mapping 복사와 public builder 전달을 확인했다.
- `ruff check .`, `ruff format --check .`: 통과.
- `mypy backend/app ai_worker`: 669개 소스 파일 통과.
- `scripts/ci/run_test.sh`: Python inventory, Alembic 단일 head `166f50617283`,
  DB function/procedure/Trigger/RLS 재도입 검사, protected-table 쓰기 검사 통과 후
  `envs/.local.env` 부재로 중단. 전체 DB·Redis 통합 파이프라인 통과를 의미하지 않는다.

합성 저장소의 rollback/commit 검증은 실제 DB Adapter의 transaction·currentness·STALE 검증을 대신하지 않는다.

## 인계 후 남은 작업

- #180: 실제 Handler 생성 인터페이스, 전용 결과 타입, 정상 결과·승인 fallback 구분 인계.
- #174: 저장 Adapter, DB 저장 계약 및 currentness·STALE 처리 기준 인계.
- 두 인터페이스 인계 후 실제 구현체 연결과 DB 통합 검증 수행.

이번 변경에는 공개 API 202 전환, 새로운 Result API, 실제 Provider 연결, 전용 payload나
저장 의미 정의를 포함하지 않는다. 현재 Guide 동기 경로는 유지한다.
