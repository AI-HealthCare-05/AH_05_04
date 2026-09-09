# Issue #170 Medication Resolver 실행 계획

## 상태

| 항목 | 값 |
| --- | --- |
| Issue | `#170` Candidate Resolver |
| 기준 | 2026-09-09 최신 `origin/develop` |
| 문서 상태 | 승인된 pure/local slice 실행 계획 |
| 즉시 구현 | Protocol, 순수 Gate, synthetic policy/Fake, 단위 테스트 |
| 통합 | `BLOCKED` — #168/#169/#171, canonical confirmed-input Decision 및 production policy Receipt 대기 |
| 공개 gate | `PUBLIC_TRACK_F=false` 유지 |

## 구현 순서

### Task 1 — 문서와 합성 정책 경계 갱신

- 설계의 `G0 전체 차단`을 `PURE_SLICE_READY / INTEGRATION_BLOCKED`로 정정한다.
- 기존 fixture를 executable production policy로 오해하지 않도록 유지한다.
- 테스트는 기존 `policy.synthetic.json`을 로드하지 않고 inline non-release policy만 생성한다.
- production loader/default threshold는 만들지 않는다.

검증: `git diff --check`, JSON parse/hash 검증.

### Task 2 — 정책 타입을 TDD로 구현

대상:

- `backend/app/services/rag/candidate_policy.py`
- `backend/app/tests_unit/rag/test_candidate_resolver.py`

먼저 잘못된 version, 길이, limit, stage weight exact coverage, RRF, finite/range,
selectable-stage subset을 `POLICY_INVALID`로 거부하는 테스트를 작성한다. 그 뒤 불변
`ResolverPolicy`와 Resolver-side validation을 최소 구현한다.
Dense selectable stage와 `release_eligible=true`도 `POLICY_INVALID`로 고정한다.

### Task 3 — Protocol과 입력 검증을 TDD로 구현

대상:

- `backend/app/services/rag/candidate_resolver.py`
- `backend/app/tests_unit/rag/test_candidate_resolver.py`

`ResolverInput`, identity/snapshot/hit, result/failure 타입, `CandidateIndexPort`,
`CandidateAttributeMatcher`, `CandidateRelevanceEvaluator`를 정의한다. 약명·함량의 blank,
whitespace, non-NFC, length 오류만
`INVALID_INPUT`인지 검증하고, index/policy version 오류는 typed failure로 분리한다.
이 Task는 저장된 확정값을 정규화하거나 `ResolverInput`으로 변환하는 mapper를 구현하지 않는다.
Candidate Index용 `CandidateSearchRequest`에는 약명·index version·limit만 허용하고 함량은 포함하지 않는다.

### Task 4 — typed hydration과 evidence 검증을 TDD로 구현

Fake Port의 단일 `hydrate(request)` 호출로 bounded Product/Ingredient hit와 provenance receipt를 전달한다.
Product 검색 stage 순서·limit·provenance 검증은 #167 `search_candidate_index(...)`가 단독 소유하고 #170은
재실행하지 않는다. Resolver는 stage별 rank/limit, index/catalog/source/normalization/embedding provenance,
identity/snapshot과 finite score를 다시 fail-closed 검증한다. 명시적 domain dependency 예외는 typed failure로
닫되 예상 밖 프로그래밍 예외는 오분류하지 않는다. `prepare_candidate_search(...)`는 #168과 strength-free 요청
allowlist만 공유한다. 제품 표시 snapshot은 Candidate Result 저장 계약과 같은 필드별 길이 상한을 검증한다.

### Task 5 — dedupe/fusion/attribute Gate를 TDD로 구현

- `(code_system, canonical_code)` dedupe
- 동일 stage 중복 signal 정규화
- deterministic RRF와 identity tie-break
- active/status, Fake matcher가 판정한 strength/form/manufacturer compatibility,
  Fake relevance evaluator와 selectable-stage hard gate
- identity snapshot 충돌 fail-closed
- matcher/evaluator를 dedupe identity당 한 번 호출하고, evaluator에는 strength-free search request만 전달하며,
  UNKNOWN/exception/malformed result를 fail-closed
- matcher에는 score/rank가 없는 Product Snapshot만 전달
- exact RRF, same-stage 중복 정규화, policy tuple 순서 독립성과 identity tie-break

입력 hit 순서를 바꿔도 결과가 같은 property를 사례 기반 테스트로 고정한다.

### Task 6 — outcome과 redaction 불변식을 TDD로 구현

- exact/alias single
- cross-stage duplicate single
- 복수 variant 및 insufficient margin → `AMBIGUOUS`
- explicit strength conflict/inactive/low relevance/dense-only → `NO_CANDIDATE`
- ingredient-only → `INGREDIENT_ONLY`
- empty/invalid input → `INVALID_INPUT`
- `SINGLE_CANDIDATE` 외 `candidate is None`
- redacted result에 top-K/rank/score 필드가 구조적으로 없음
- partial stage 성공 뒤 실패, matcher/evaluator 예외 detail과 query 원문 비노출
- 내부 후보는 평가·진단 evidence이며 #171 Result row로 자동 투영하지 않음. `NO_CANDIDATE | INGREDIENT_ONLY |
  INVALID_INPUT`의 persisted/result count는 승인 Target대로 0

### Task 7 — 독립 검토와 전체 검증

- 설계·구현·테스트가 Target/Issue 경계를 바꾸지 않는지 코드 리뷰한다.
- `ai_worker`, SQLAlchemy, Redis, Outbox import가 없는지 검사한다.
- targeted pytest, Ruff, mypy, fixture JSON/hash, `git diff --check`를 실행한다.
- 이어서 `CONTRIBUTING.md`의 repository-required format, type, full test checks를 실행한다.
- 검토에서 blocking finding이 있으면 수정 후 같은 검증을 다시 실행한다.

## 테스트 명령

```bash
uv run pytest backend/app/tests_unit/rag/test_candidate_resolver.py -q
uv run ruff check \
  backend/app/services/rag/candidate_resolver.py \
  backend/app/services/rag/candidate_policy.py \
  backend/app/tests_unit/rag/test_candidate_resolver.py
uv run mypy \
  backend/app/services/rag/candidate_resolver.py \
  backend/app/services/rag/candidate_policy.py
uv run ruff format . --check
uv run ruff check .
uv run mypy backend/app ai_worker
bash scripts/ci/run_test.sh
git diff --check
```

Worker-side contract suite, PostgreSQL adapter parity, Candidate Search transaction 및 HOLDOUT/SAFETY 평가는
이번 pure slice의 완료 조건이 아니다. 실행하지 않은 통합 검증은 `PASS`가 아니라 명시적인 후속 작업으로
보고하고 Resolver HOLDOUT/SAFETY는 `NOT_RUN / INTEGRATION_BLOCKED`로 기록한다.

## 후속 통합 인계

- #168의 async repository/service는 `prepare_candidate_search(...)`로 strength-free 요청을 만들고 물리 조회를
  한 번 수행한다. Product 결과는 #167 `search_candidate_index(...)`의 stage 순서·limit·Catalog·Source·
  `(Source Snapshot ID, source version)`·normalization·embedding provenance 검증을 통과한 뒤 bounded immutable
  evidence snapshot으로 변환한다.
  현재 동기 `CandidateIndexPort`는 이 snapshot을 `hydrate(request)` 한 번으로 넘기는 in-memory 경계이며 DB
  adapter가 직접 구현하거나 내부에서 async 호출을 숨기는 Protocol이 아니다.
- #169/#171 통합 전에 확정 저장값의 NFC·공백 규칙과 production 입력 길이를 OCR·Backend·RAG owner Decision으로
  확정한다. 현재 저장 경로가 canonical Resolver 입력을 보장하지 않으므로 mapper/저장 계약을 추정 구현하지 않는다.
- inline synthetic `maximum_input_length=100`을 production 기본값으로 승격하지 않는다. 현재 DTO/DB의
  `medication_name=255`, `strength_text=100` 상한과 정렬된 versioned policy 및 기존 행 검증이 필요하다.
- 위 조건이 없으면 #171의 `INVALID_INPUT` 공개 mapping을 추정 구현하지 않고 integration gate를 닫아 둔다.
  공개 error/status 의미는 별도 공유 계약에서 확정한다.
- Resolver `internal_candidates`는 평가·진단용이다. #171은 승인 Target에 따라 `NO_CANDIDATE |
  INGREDIENT_ONLY | INVALID_INPUT`을 Result row 0건으로 투영하며 내부 후보를 자동 전량 저장하지 않는다.

## 완료와 중지 조건

Pure/local slice 완료 조건:

- Protocol·순수 Gate·Fake 단위 테스트가 구현되고 targeted checks가 통과한다.
- business outcome과 execution failure가 분리된다.
- 외부 후보 최대 1개와 비-single outcome redaction이 테스트로 고정된다.
- production loader, DB/API integration, 환자 데이터가 diff에 없다.

다음이 필요하면 pure slice를 넘은 것이므로 구현을 중지하고 해당 owner/Receipt로 넘긴다.

- 공유 DTO/status/error/DB constraint 변경
- 실제 Candidate Index repository 또는 active pointer
- Prescription Version ownership/currentness 조회
- 확정 입력 Unicode/공백 정규화 또는 DTO·DB 길이 계약 변경
- Candidate Search Finalizer transaction
- production threshold나 release-eligible policy 결정
