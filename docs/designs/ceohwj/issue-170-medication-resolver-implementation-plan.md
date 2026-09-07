# Issue #170 Medication Resolver 실행 계획

## 상태

| 항목 | 값 |
| --- | --- |
| Issue | `#170` Candidate Resolver |
| 기준 | `origin/develop`의 `899c2dc` 위 rebase 완료 |
| 문서 상태 | 구현 순서 기록. 공유 계약 또는 Current Runtime 정본이 아님 |
| 즉시 완료 범위 | 설계 snapshot, LOCAL 합성 fixture, non-release policy proposal |
| Production 구현 | `BLOCKED` — G0 및 G1–G7 승인 전 착수 금지 |
| 공개 gate | `PUBLIC_TRACK_F=false` 유지 |

## 이미 완료한 준비 작업

- [x] Resolver와 #171 Finalizer의 경계를 설계 snapshot으로 기록했다.
  - Resolver business decision은 `SINGLE_CANDIDATE`다.
  - #171 Finalizer만 이를 lifecycle `READY`와 공개 후보 1개로 투영한다.
- [x] 실제 약명·환자·처방·OCR 원문을 포함하지 않는 LOCAL Draft/DEV decision matrix를 작성했다.
- [x] LOCAL/non-release synthetic policy envelope과 canonical JSON SHA-256 proposal을 작성했다.
- [x] Resolver 직접 입력을 `medication_name`과 nullable `strength_text`로 제한하고, `form_text`가 직접 입력이
  아님을 fixture로 고정했다.

준비 산출물:

- [설계 snapshot](issue-170-medication-resolver-design.md)
- [합성 decision matrix](../../../tests/fixtures/rag/resolver/draft_decision_matrix.json)
- [합성 policy proposal](../../../tests/fixtures/rag/resolver/policy.synthetic.json)
- [fixture 사용 범위](../../validation/rag/resolver/README.md)

이 산출물은 문서/합성 검증을 위한 Draft일 뿐이고 runtime loader, release Receipt 또는 production
threshold의 정본이 아니다.

## 차단 Gate

| Gate | 현재 상태 | 해소에 필요한 artifact | 해소 뒤 가능 작업 |
| --- | --- | --- | --- |
| G0 dependency | `BLOCKED` | #166 Source/Catalog completion Receipt, 승인된 #167 interface/Decision, #168 active Candidate Index Receipt, #169 active Medication Snapshot Receipt | Resolver integration 준비 |
| G1 input/provenance | `TBC (BLOCKED BY G0)` | input owner·normalization·version Decision 및 OCR Receipt 갱신 | input mapper/retrieval facade |
| G2 stages/result method | `TBC (BLOCKED BY G0)` | search-stage·Ingredient diagnostic·`result_method` Decision | stage adapter/method mapping |
| G3 identity/fusion | `TBC (BLOCKED BY G0)` | Product Identity dedupe·deterministic fusion policy | pure fusion kernel |
| G4 attributes | `TBC (BLOCKED BY G0)` | strength/form/status mapping Decision | compatibility gate |
| G5 outcome | `TBC (BLOCKED BY G0)` | threshold/margin/dense policy와 Single Candidate evaluation Receipt | outcome classifier |
| G6 finalization | `TBC (BLOCKED BY G0)` | #171 transaction/count/mapping Decision | #171 handoff contract; persistence 구현은 #171 소유 |
| G7 failures/receipt | `TBC (BLOCKED BY G0)` | failure mapping·public error·Receipt schema Decision | failure mapper/contract Receipt |

현재 Source Receipt는 실제 binding이 연결되기 전까지 `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`이며,
OCR 입력 Receipt는 `PRESCRIPTION_VERSION_NOT_IMPLEMENTED` 및
`TARGET_STRENGTH_MAPPING_NOT_FROZEN`을 유지한다. 이 세 차단 코드를 축약하거나 성공 상태로 바꾸지 않는다.

## 구현 순서

### Phase A — 승인 전, 현재 완료

1. 설계/Receipt snapshot
2. 합성 Draft/DEV fixture
3. non-release policy envelope/hash proposal

Phase A 이후에는 runtime code를 추가하지 않는다. 합성 fixture의 outcome, reason, DB projection은 G0–G7
승인 전까지 `NON_AUTHORITATIVE_DRAFT` 또는 `TBC`다.

### Phase B — G0–G7 승인 후

1. `ResolverPolicy`의 explicit version/hash lookup과 fail-closed validation을 TDD로 구현한다.
2. Backend async retrieval facade와 Candidate read Protocol을 구현한다. facade는 #168 read port의 hydration,
   provenance/shape validation과 typed internal failure만 소유한다.
3. DB/Worker import 없이 `ResolverEvidence + ResolverPolicy`만 받는 pure kernel을 구현한다.
4. `Exact → Alias → Ingredient diagnostic → Trigram/Edit → optional Dense` 호출 순서, stable Product Identity
   dedupe, deterministic fusion을 구현한다.
5. attribute compatibility와 Single Candidate Gate를 구현한다.

필수 불변식:

- `SINGLE_CANDIDATE`만 외부 후보 1개이고 다른 business outcome은 0개다.
- Ingredient diagnostic은 Product 후보/fusion/count에 포함하지 않는다.
- unknown version, malformed hit, non-finite score, repository failure는 `INVALID_INPUT`으로 위장하지 않는다.
- Resolver는 Candidate Search, Result, Identification, DB lifecycle enum 또는 display/selectable flag를 생성하지 않는다.

### Phase C — 계약·adapter·application integration

1. 승인된 JSON contract fixture를 Backend unit suite와 Worker-side schema/hash suite에서 재생한다.
2. #168 PostgreSQL adapter가 같은 fixture에 대해 Fake와 같은 business outcome을 내는지 검증한다.
3. #169 활성 immutable Prescription Version Medication에서만 `ResolverInput`을 만든다.
4. #171 owner가 Resolver outcome/failure를 Search/Result/status/flag로 매핑하고 transaction currentness를
   재검증한다. #170 범위는 mapping contract·fixture·review이며 #171 persistence 구현을 대체하지 않는다.
5. Source/Index/Prescription/Finalizer Receipt, HOLDOUT 및 SAFETY evaluation이 모두 연결된 경우에만
   integration completion을 선언한다.

## 검증 순서

1. Phase B: policy/retrieval/kernel의 targeted unit tests와 import-isolation tests
2. Phase B/C: synthetic JSON fixture/schema/hash parity와 privacy sentinel tests
3. Phase C: #168 adapter parity, #169 currentness, #171 transaction/concurrency integration tests
4. Phase C: Product/Ingredient precision, Candidate recall, abstention, execution failure, multi-gold,
   zero-denominator를 분리한 HOLDOUT/SAFETY Receipt

`zero denominator`와 미실행 integration/HOLDOUT은 PASS가 아니라 `INCONCLUSIVE` 또는
`NOT_IMPLEMENTED`로 기록한다. wrong-single, Ingredient Product 승격, 공개 score/top-K/원문 노출은
SAFETY regression의 Critical 0건 조건이다.

## 중지 조건

다음 중 하나가 발생하면 해당 Phase를 중지하고 owner Decision/Target을 먼저 갱신한다.

- #168 Candidate projection 또는 Ingredient diagnostic이 승인된 #167 interface와 충돌함
- normalization owner/version 또는 nullable strength의 물리 mapping이 미확정임
- internal failure와 공개 lifecycle/error mapping이 합의되지 않음
- shared enum, DTO, route, DB constraint, transaction order의 변경이 필요함
- 실제 환자·처방·OCR 원문 fixture가 필요해짐

## 후속 담당 경계

- #170: Resolver policy/facade/pure kernel, synthetic contract fixture, Receipt evidence
- #168: Candidate Index PostgreSQL adapter/read port/active pointer
- #169: immutable Prescription Version Medication와 active pointer
- #171: Candidate Search/Result persistence, finalization transaction, confirm/reject/Identification lifecycle

공유 계약 변경은 관련 Target/Decision, OpenAPI·DTO, migration, automated contract/integration test,
`docs/contracts/README.md`, Receipt를 같은 focused PR에서 함께 갱신한다.
