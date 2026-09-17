# Assessment·Eligibility Authority Persistence 계약 v1 (#712)

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed / 구현·로컬 검증 완료 · 담당 리뷰 대기 |
| 추적 Issue | [#712](https://github.com/AI-HealthCare-05/AH_05_04/issues/712) |
| 선행 결정 | [`PD-722-20260917`](../../../governance/decisions/2026-09-17-evidence-assessment-validity.md) (Approved, PR #725 merged) |
| 선행·관련 | [#713 REQUEST Authority Persistence](./request-authority-persistence-v1.md), [#697 Guide Retrieval Composition](./guide-retrieval-composition-v1.md), [#180 Guide Evidence Handoff](./guide-evidence-handoff-v1.md), #178 |
| 소비자 | 후속 read-only Assessment·Eligibility Authority Reader, 이후 `GuideEvidenceHandoffRequest` 조립 (#180) |

---

## 1. 목적과 권위 경계

`GuideEvidenceHandoffRequest`는 selection마다 다음 다섯 값을 요구한다.

```text
eligibility_receipt_ref
assessment_artifact_ref
verifier_artifact_ref
assessment_valid_from
assessment_valid_until
```

`PD-722` §2가 확인한 대로 이 다섯 값에 exact-bind 가능한 기존 authoritative persistence는 0건이다.
본 계약은 그 공백만 메운다. 즉 **Production Evidence Gate를 통과한 selected hit 시점에 실제로 성립한
평가 사실**을 immutable historical artifact로 발급·영속화하고, 후속 Reader가 재구성 없이 되돌려 받을
exact lookup key를 확정하는 것까지가 범위다.

본 계약은 다음을 포함하지 않는다.

```text
Assessment·Eligibility Authority Reader (후속 Issue)
GuideEvidenceHandoffRequest 조립 · VerifiedGuideEvidenceHandoff 생성
content hydration · KnowledgeChunk text reader
#180 runtime orchestration
Guideline Generator · Citation Authorization · Frontend/API
PUBLIC_TRACK_F 활성화
```

### 현재 Track F authority 진행 상태

| 단계 | 상태 |
| --- | --- |
| REQUEST Guard / Source / Member Production Reader | DONE (#709, `ai_worker/adapters/sqlalchemy_guide_evidence_authority.py`) |
| Assessment / Eligibility Authority Persistence | 본 PR (#712) |
| Assessment / Eligibility Reader | NOT IMPLEMENTED |
| GuideEvidenceHandoff authoritative assembly | NOT IMPLEMENTED |
| #180 orchestration | NOT IMPLEMENTED |

#709 Production Reader는 REQUEST 단위 Guard·Source·Member authority를 읽는다. 본 계약이 발급하는
selected hit 단위 Assessment·Eligibility authority와는 책임 경계가 다르며, 이를 읽는 Reader는 아직
구현되지 않았다.

### 이 표가 대체하지 않는 것

| 기존 persistence | 실제 의미 | authority가 아닌 이유 |
| --- | --- | --- |
| `catalog_source_approval` (`valid_from`, `expires_at`) | Catalog 빌드 전용 운영자 승인 | Runtime Evidence 사용까지 포함한다고 증명되지 않았다 (`PD-722` §2.1) |
| `rag_source_snapshot` (`verification_status='CURRENT'`) | 현재 유효한 Snapshot 상태 | 과거 평가 시점의 eligibility 판정이 아니다 |
| `retrieval_run.receipt_hash` / `search_receipt_hash` | 검색 실행 receipt | eligibility 판정도, assessment 유효기간도 아니다 |
| `ai_job_execution_context` | 실행 context | validity window 열이 없다 |

`CURRENT` Snapshot 상태를 사후 조회해 historical validity window를 재구성하는 행위는 금지한다
(`PD-722` §6.3).

---

## 2. 저장 구조

Migration `712a1b2c3d4e`가 `rag_evidence_authority` 한 표를 추가한다. 발급 단위는 **selected hit 한 건**,
즉 `(retrieval_run_id, knowledge_chunk_id)`이다.

| 열 그룹 | 열 | 비고 |
| --- | --- | --- |
| 식별 | `id` | UUIDChar PK |
| 논리 identity | `retrieval_run_id`, `knowledge_chunk_id` | UNIQUE. 복합 FK → `retrieval_hit` PK, `ON DELETE RESTRICT` |
| Source binding | `source_snapshot_id`, `source_snapshot_member_id` | FK `ON DELETE RESTRICT` |
| Source binding | `source_code`, `source_version` | 각각 nonblank CHECK |
| Content binding | `content_sha256` | 64-hex CHECK |
| Eligibility receipt | `eligibility_receipt_artifact_code`, `eligibility_receipt_version`, `eligibility_receipt_sha256` | 64-hex CHECK |
| Assessment artifact | `assessment_artifact_code`, `assessment_artifact_version`, `assessment_artifact_sha256` | 3열 UNIQUE + index, 64-hex CHECK |
| Verifier | `verifier_artifact_code`, `verifier_artifact_version`, `verifier_artifact_sha256` | 64-hex CHECK |
| Validity policy | `validity_policy_artifact_code`, `validity_policy_version`, `validity_policy_sha256` | 64-hex CHECK |
| Validity | `evaluated_at`, `assessment_valid_from`, `assessment_valid_until` | 모두 timestamptz |
| 감사 | `created_at` | server default `now()` |

구간 제약 두 개를 표 수준에서 고정한다.

```sql
CHECK (assessment_valid_from < assessment_valid_until)
CHECK (evaluated_at >= assessment_valid_from AND evaluated_at < assessment_valid_until)
```

즉 half-open interval `assessment_valid_from <= evaluated_at < assessment_valid_until`
(`PD-722` §6.1)을 DB가 직접 강제하므로, 구간을 벗어난 authority는 애초에 저장되지 않는다.

Trigger·RLS·Stored Procedure·사용자 정의 DB 함수는 추가하지 않는다 ([AGENTS.md](../../../../AGENTS.md)).

### 2.1 append-only 권한과 downgrade 거부

`rag_evidence_authority`는 historical 증거이므로 runtime role에 `SELECT, INSERT`만 부여한다
(`infra/python/provision_database_roles.py`의 `RUNTIME_APPEND_ONLY_TABLES`). 쓰기는 보호 테이블
경계에 따라 `backend/app/repositories/rag_evidence_authority_repository.py` 한 곳만 승인된다.

`downgrade()`는 무조건 drop하지 않는다. `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE`로 먼저 잠근 뒤
행 존재를 확인하고, 비어 있지 않으면 `RuntimeError`로 거부한다 (#713·#596·#674와 같은 패턴).

---

## 3. Artifact identity

네 ref 모두 기존 `ImmutableArtifactRef` 의미(`artifact_code`, `version`, `content_sha256`)를 따른다.
`content_sha256`은 semantic projection의 RFC 8785 JCS canonical digest이며, DB PK나 `created_at`처럼
비결정적인 값은 projection에 넣지 않는다. 새 hash domain을 만들지 않는다.

계약과 계산은 Backend·AI Worker가 공유하는 순수 package `rag_runtime/evidence_authority.py`가
소유한다. Backend production 코드는 `ai_worker.*`를 직접 import하지 않으며 `PD-175-20260910` 경계와
`ALLOWED_AI_WORKER_MODULES`를 변경하지 않는다.

| ref | `artifact_code` | `version` | projection 내용 |
| --- | --- | --- | --- |
| `validity_policy_ref` | `evidence-assessment-validity` | `v1` | `PD-722` §7.1 정본 payload (golden digest 고정) |
| `verifier_artifact_ref` | `postgresql_evidence_eligibility_verifier` | `v1` | verifier contract·semantics·version |
| `eligibility_receipt_ref` | `production_evidence_eligibility_receipt` | `v1` | run·chunk·snapshot·member·source·content·`evaluated_at`·verifier ref·`eligibility_decision="ELIGIBLE"` |
| `assessment_artifact_ref` | `production_evidence_assessment` | `v1` | run·chunk·eligibility ref·validity policy ref·validity window·`evidence_gate_status="SUCCEEDED"` |

### 3.1 caller가 고를 수 없는 값

Issue #712가 금지한 항목을 구조로 막는다.

- `artifact_code`·`version`은 계약 상수이고 `content_sha256`은 projection digest이므로 **caller가
  임의 identity를 주입할 수 없다**.
- `IssueAssessmentAuthorityRequest`에는 **verifier ref 입력 자리가 없다**. verifier identity는 판단을
  수행한 issuer가 `compute_verifier_artifact_ref()`로 직접 계산한다 ("caller가 전달한 verifier ref
  신뢰" 금지). Reader도 ref를 생성하지 않는다.
- DB PK만으로 artifact identity를 인정하지 않는다. 조회는 3열 exact equality만 제공한다.

---

## 4. Validity 산출

`PD-722` §4를 그대로 구현한다.

```text
assessment_valid_from  = authoritative evaluated_at
assessment_valid_until = min(evaluated_at + 24h, *applicable_upper_bounds)
```

- 모든 datetime은 timezone-aware UTC여야 하며 naive·비-UTC는 fail-closed 거부한다.
- `24h`는 승인된 `EvidenceAssessmentValidityPolicy v1`의 operational maximum ceiling이다. 의학적·
  데이터적 최신성을 주장하지 않는다 (`PD-722` §5).
- 존재하지 않는 상한을 만들어내지 않는다. Catalog 전용 만료일이나 Job lease는 결합 대상이 아니다.
- **Retry는 window를 연장하지 않는다.** 최초 `T0`를 동결하며 `T0 + 10분`에 재시도해도
  `[T0, T0+24h)`를 유지한다 (`PD-722` §6.2).

---

## 5. Issuer 경계

`ai_worker/tasks/rag/evidence_authority_issuer.py`의
`issue_assessment_eligibility_authority(request, store)`가 유일한 발급 경로이며 다음 순서로 fail-close한다.

```text
1. request shape 검증 (UUID · nonblank source · 64-hex content_sha256)
2. selected hit 확인            → 없으면 SELECTED_HIT_NOT_FOUND / 미선택이면 HIT_NOT_SELECTED
3. source·member·content exact binding 확인
                                → SOURCE_BINDING_MISMATCH / CHUNK_CONTENT_MISMATCH
4. 기존 authority 확인          → 동일 의미면 그대로 반환, 다르면 AUTHORITY_IDENTITY_CONFLICT
5. validity window 산출         → INVALID_VALIDITY_WINDOW
6. 네 artifact projection 계산
7. 멱등 영속화
```

Store 경계는 `EvidenceAuthorityStorePort` Protocol이고, production 구현은
`RagEvidenceAuthorityRepository`다.

### 5.1 binding 정본

`get_chunk_source_binding`은 `rag_knowledge_index_member`가 색인 시점에 기록한 `content_hash`를
정본으로 쓰고, `knowledge_chunk.content_hash`와 **일치할 때만** binding을 인정한다.
`knowledge_chunk.content_hash`는 nullable이며 hydration 이후 채워지므로 단독으로는 과거 selection의
불변 binding 정본이 될 수 없다. 이 규칙은 이미 authoritative한
`PostgreSqlEvidenceEligibilityVerifier._is_provenance_matching`과 같다.

`rag_knowledge_index_member`의 `UNIQUE(knowledge_index_id, knowledge_chunk_id)` 덕분에 lookup 결과는
최대 1행이며 ambiguous binding이 생기지 않는다.

---

## 6. 후속 Reader가 사용할 exact lookup key와 projection

Reader는 다음 두 가지 exact key만 사용한다. latest/CURRENT fallback은 두지 않으며, 정상 no-row만
`None`이고 손상 레코드는 fail-closed다.

| lookup | key | primitive |
| --- | --- | --- |
| selection 단위 조회 | `(retrieval_run_id, knowledge_chunk_id)` | `get_authority_by_identity` |
| artifact 단위 조회 | `(assessment_artifact_code, assessment_artifact_version, assessment_artifact_sha256)` | `get_authority_by_assessment_ref` |

반환 projection은 `PersistedEvidenceAuthority`이며 재구성 없이 다음을 그대로 돌려준다.

```text
id
retrieval_run_id · knowledge_chunk_id
source_snapshot_id · source_snapshot_member_id · source_code · source_version · content_sha256
eligibility_receipt_ref · assessment_artifact_ref · verifier_artifact_ref · validity_policy_ref
evaluated_at · assessment_valid_from · assessment_valid_until
created_at
```

datetime은 모두 timezone-aware UTC로 정규화해 돌려준다.

Reader는 **현재 시점의 적격성(viability/currentness/revocation)을 여기서 판정하지 않는다.** 과거
authority는 불변 사실이고, 현재 사용 가능 여부는 소비 계층의 별도 검사 책임이다 (`PD-722` §6.3).

---

## 7. Fail-closed 규칙

| 상황 | 결과 |
| --- | --- |
| selected hit 없음 / `selected=False` | `SELECTED_HIT_NOT_FOUND` / `HIT_NOT_SELECTED` |
| snapshot·member·source 불일치 | `SOURCE_BINDING_MISMATCH` |
| index member와 chunk의 content hash 불일치 | binding 미인정 → `SOURCE_BINDING_MISMATCH` |
| 요청 content hash 불일치 | `CHUNK_CONTENT_MISMATCH` |
| 동일 identity에 다른 내용 | `AUTHORITY_IDENTITY_CONFLICT` (덮어쓰지 않음) |
| 구간 역전·영구간 | `INVALID_VALIDITY_WINDOW` (DB CHECK도 함께 거부) |
| naive·비-UTC datetime | `DATETIME_NOT_UTC` |
| 영속 레코드의 ref 손상 | `CORRUPT_AUTHORITY_RECORD` |

동일 입력 재시도는 오류가 아니라 기존 레코드를 그대로 반환한다(멱등).

---

## 8. 검증

- `ai_worker/tests/rag/test_evidence_authority_issuer.py` — issuer 계약 10건
  (PD-722 golden digest, 구간 규칙, retry 비연장, selected hit 강제, binding 강제, 멱등, 충돌,
  cross-run 격리, malformed ref, writer-owned verifier identity).
- `backend/app/tests/rag/test_rag_evidence_authority_repository.py` — 실제 PostgreSQL 왕복 11건
  (JCS byte 동등성, selected hit·binding 실제 스키마 조회, 미선택 구분, hash 불일치 거부,
  round-trip exact 보존, retry 비연장, 충돌 fail-closed, cross-run/chunk 교차 사용 불가,
  digest 변조 DB 거부, artifact_code 변조 읽기 fail-closed, ref 형식).
- `tests/integration/rag/test_database_role_provisioning.py` — append-only 권한 경계.

Reader·Handoff·런타임 연결은 여전히 미구현이며, 본 계약의 Current 승격은 별도다.
