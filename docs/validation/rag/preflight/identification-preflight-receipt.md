# RAG-12 Medication Identification Preflight 판정 증빙 (Issue #173)

| 항목 | 값 |
| --- | --- |
| Issue | `#173` |
| 브랜치 | `feat/173-rag-identification-preflight` |
| 기준 commit | `083f69eb` (`origin/develop`) |
| 검증일 | 2026-09-09 |
| 구현 담당자 | 정현우 (`@ceohwj`) |
| 담당 리뷰어 | 송은영 (`@phina-io`) — RAG-12-API 소비 계약 |
| Safety 리뷰 | 권가빈 (`@hazelnutflavoured`) |
| 공개 게이트 | `PUBLIC_TRACK_F_ENABLED=false` (변경 없음) |

## 이 증빙이 뜻하지 않는 것

이 문서는 순수 판정 kernel의 결정성·범위 증빙이다. 다음을 뜻하지 않는다.

- Production 활성 가능성. `#174` RAG-12-API, `#180` RAG-16, E2E와 외부 승인이 모두 미충족이다.
- Preflight 상태의 실제 DB 사영 정확성. 이 slice는 상태를 입력으로 받는다.
- Runtime Bundle 내용 검증. `#175` RAG-12A 미구현. 이 slice는 bundle 식별자 일치만 본다.
- 통합 준비 완료. 통합 테스트 연결일(`2026-09-11`) 기준 차단 코드는
  `BLOCKED_BY_IDENTIFICATION_CONTRACT`를 유지한다.

## 실행한 검사

### Issue 지정 테스트 명령

```text
$ uv run pytest ai_worker/tests/rag/test_identification_preflight.py -q
39 passed in 0.06s

$ uv run pytest tests/contract/rag/test_preflight_decision_contract.py -q
30 passed in 0.07s
```

### 정적 검사

```text
$ uv run ruff check .
All checks passed!

$ uv run ruff format . --check
564 files already formatted

$ uv run mypy backend/app ai_worker
Success: no issues found in 471 source files
```

### 기본 CI 회귀

```text
$ bash scripts/ci/run_test.sh
tests/migration                                  119 passed in 27.57s
backend/app + tests/contract + tests/integration/rag + 선별 OCR 통합
                                                 1272 passed, 59 skipped in 82.97s
실제 Redis 선별 통합                              18 passed in 3.08s
ai_worker/tests/core|ocr|rag|evaluation          2429 passed, 8 skipped in 148.52s
TOTAL coverage 94%
```

로컬 PostgreSQL·Redis 컨테이너(`postgres:17-alpine`, `redis:alpine`)로 실행했다. 이 slice는 DB·Redis
경로를 바꾸지 않으므로 위 결과는 회귀 부재 확인용이다.

## 실행하지 않은 검사와 이유

| 검사 | 이유 |
| --- | --- |
| `evals/` 회귀 | 이 slice는 Retrieval·Rerank·Composer·Answer 품질 경로를 바꾸지 않는다. 판정 kernel은 evals dataset을 소비하지 않는다 |
| OpenAPI 회귀 | endpoint·Router·DTO 변경 0건 |
| Migration 테스트 추가 | Migration·Repository 변경 0건 |
| E2E | RAG-11 UI(`#131`)와 RAG-12-API(`#174`) 미구현 |

## 필수 테스트 대응

| Issue 요구 | 테스트 | 결과 |
| --- | --- | --- |
| 전체 MATCHED → PASS | `test_all_matched_returns_pass`, fixture `PF-001` | 통과 |
| 하나라도 non-MATCHED → Identification Fallback | `test_single_non_matched_blocks_execution` (6개 상태 parametrize), fixture `PF-002`·`PF-003`·`PF-004` | 통과 |
| Retrieval/Provider 호출 0건 | `test_kernel_has_no_execution_ports`, `test_kernel_imports_are_stdlib_only` | 통과 |
| 현재 Version 변경 → Stale Fallback | `test_active_version_change_returns_stale_fallback`, fixture `PF-005`·`PF-007` | 통과 |
| Medication 0개 | `test_empty_medications_fails_closed`, fixture `PF-008` | 통과 |
| 중복 ID | `test_duplicate_medication_id_fails_closed`, `test_duplicate_identification_id_fails_closed`, fixture `PF-010` | 통과 |
| 알 수 없는 enum | `test_unknown_state_fails_closed`, fixture `PF-009` | 통과 |
| 순서가 달라도 동일 hash/decision | `test_input_order_does_not_change_manifest_or_decision`, `test_decision_matrix_case_is_order_independent` (fixture 12건 전체) | 통과 |
| 동일 입력 재시도 → 동일 결과, side effect 0건 | `test_repeated_evaluation_is_idempotent`, `test_outcome_is_immutable` | 통과 |

## 완료 기준 대비 현황

| 완료 기준 | 상태 |
| --- | --- |
| Guide와 Chat `ROUTINE`이 동일 decision contract를 소비한다 | 부분. 공통 kernel과 typed 계약은 제공. 실제 배선은 `#174`·`#180` |
| non-MATCHED에서 일반 Rule·RAG·Provider를 실행하지 않는다 | 판정 수준 충족. 실행 경로 강제는 `#180` |
| Identification Fallback과 Stale Fallback이 서로 다른 reason code다 | 충족 (`REVIEW_REQUIRED`… vs `EXECUTION_CONTEXT_STALE`) |
| diff에 DB/endpoint/lock/transaction 구현이 0건이다 | 충족. `test_kernel_imports_are_stdlib_only`가 고정 |
| pure unit/contract test가 실 Provider/credential 없이 통과한다 | 충족 |

Issue를 Close하지 않는다. `#174`·`#131`·`#175` 미구현으로 통합·Close 기준을 충족하지 못한다.

## 리뷰 필요 미확정 항목

1. Stale이 Identification Fallback보다 앞서는 판정 우선순위 (고정 실행 Graph가 순서를 정하지 않았다)
2. Identification Fallback 대표 reason이 계약 표기 순서를 따르는 규칙
3. 구조 검증 실패를 `IDENTIFICATION_FALLBACK/REVIEW_REQUIRED`로 사영하는 선택
4. `#174`에서 Backend가 이 kernel을 소비할 방식

세 항목 모두 승인된 Decision이 아니다. `#174` 병합 전에 확정한다. 그때까지 `identification_reasons`,
`stale_signals`, `validation_codes`를 환자 문구나 공개 DTO에 직접 매핑하지 않는다.

## 합성 데이터 확인

`tests/fixtures/rag/preflight/decision_matrix.json`은 합성 UUID와 `SYNTHETIC-` 접두 canonical code만
쓴다. 약품명·함량·보험코드·검색 원문·환자 식별자를 포함하지 않으며,
`test_fixture_carries_no_real_identity_or_insurance_code`가 이를 고정한다. manifest payload에도
약품명과 query 문자열이 들어가지 않는다.
