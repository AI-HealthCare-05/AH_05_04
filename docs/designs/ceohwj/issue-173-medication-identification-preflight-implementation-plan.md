# Issue #173 RAG-12 Medication Identification Preflight 구현 계획

동반 설계: [단위 설계](./issue-173-medication-identification-preflight-design.md)

## 범위 고정

| 구분 | 내용 |
| --- | --- |
| 포함 | 순수 판정 kernel, 결정 matrix fixture, 단위·계약 테스트, 설계·증빙 문서 |
| 제외 | Repository·Migration·Snapshot persistence, HTTP endpoint·Router·OpenAPI, lock·transaction·Job·Outbox, Retrieval·Composer·Provider 호출, Candidate Search 상태 사영, fallback 문구 |

`docs/contracts/` 아래 문서는 바꾸지 않는다. 이 slice는 확정 Target을 구현할 뿐 공유 계약을
변경하지 않는다. `PUBLIC_TRACK_F_ENABLED`는 건드리지 않는다.

## 단계

1. `rag_runtime/identification_preflight.py` (최상위 공용 패키지 `rag_runtime/__init__.py`)
   - enum 4종: `MedicationPreflightState`, `PreflightDecision`, `PreflightReason`,
     `PreflightExecutionStatus`. 추가로 진단용 `PreflightStaleSignal`, `PreflightValidationCode`
   - frozen dataclass 입력 3종 + 요청 1종, 출력 1종, 사영 결과 `PreflightStaleProjection` 1종
   - `evaluate_medication_identification_preflight(request)` 단일 공개 함수
   - `canonical_preflight_manifest_hash(request)` 공개 helper (식별 스냅샷 provenance 포함, 계약 테스트와 `#174`가 재사용)
   - `project_preflight_stale_signal(signal)` 및 `project_preflight_stale_signals(signals)` Downstream 사영 helper (`safety-result-v2.md` 정본 연계)
   - `backend/app/Dockerfile` 및 `ai_worker/Dockerfile`에 `COPY ./rag_runtime ./rag_runtime` 반영
2. `ai_worker/tests/rag/test_identification_preflight.py`
   - Issue 필수 테스트 6항목 + 구조 검증 분기별 fail-closed + 식별 provenance 변경 시 manifest_hash 변경 회귀 테스트
3. `tests/fixtures/rag/preflight/decision_matrix.json`
   - 합성 결정 matrix. 환자 식별 가능 값과 실제 제품명·보험코드 0건
4. `tests/contract/rag/test_preflight_decision_contract.py`
   - `rag-runtime-v1.md` 고정 Graph에서 fallback 어휘를 파싱해 enum과 exact-match
   - `safety-result-v2.md` 정본에서 복합 STALE 우선순위 파싱 및 exact-match 고정
   - module import 집합이 stdlib뿐임을 고정
   - fixture matrix 전체를 kernel에 통과시켜 decision·reason·hash 안정성 확인
   - 식별 provenance 변경 시 manifest_hash 변경 불변식 검증
5. `docs/validation/rag/preflight/identification-preflight-receipt.md`
   - 실행한 검사와 그 출력, 실행하지 않은 검사와 이유

## 검증 명령

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy backend/app ai_worker
uv run pytest ai_worker/tests/rag/test_identification_preflight.py -q
uv run pytest tests/contract/rag/test_preflight_decision_contract.py -q
```

`bash scripts/ci/run_test.sh`는 PostgreSQL·Redis 컨테이너를 요구한다. 이 slice는 DB·Redis 경로를
바꾸지 않으므로 기본 CI 범위 회귀만 확인하고, 실행 여부는 증빙 문서에 그대로 기록한다.
`ai_worker/tests/rag/`는 `docs/testing.md` 기준으로 기본 CI 실행 범위가 아니므로 위 명령을
Issue 완료 기준의 명시 증빙으로 남긴다.

## 리뷰 요청 항목

1. Stale이 Identification Fallback보다 앞서는 우선순위
2. Identification Fallback 대표 reason이 계약 표기 순서를 따르는 규칙
3. 구조 검증 실패를 `IDENTIFICATION_FALLBACK/REVIEW_REQUIRED`로 사영하는 선택
4. `#174`에서 kernel을 Backend가 소비할 방식 (직접 import vs 공용 package 승격)

1·2·3은 승인된 Decision이 아니다. `#174` 병합 전 확정한다.

## 통합 차단 코드

`#174` RAG-12-API, `#131` RAG-11 UI, `#175` RAG-12A Runtime Bundle이 미구현이므로 통합 테스트
연결일(`2026-09-11`)에는 `BLOCKED_BY_IDENTIFICATION_CONTRACT`를 유지한다. 이 slice의 통과가
Production 활성 조건을 충족시키지 않는다.
