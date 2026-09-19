# Per-request REQUEST Guard Runtime Binding 계약 v1 (#806)

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed / 구현 초안·로컬 검증 진행 · 담당 리뷰 대기 |
| 추적 Issue | [#806](https://github.com/AI-HealthCare-05/AH_05_04/issues/806) |
| 선행·관련 | `PD-799-20260918`, [#713 REQUEST Authority Persistence](./request-authority-persistence-v1.md), #810 |
| 구현 owner | `@ceohwj` |
| required reviewer | `@phina-io` |

## 1. 목적과 범위

이 계약은 기존 #713 REQUEST authority의 의미를 변경하지 않고, authoritative evaluator가 발행한
**per-request REQUEST Guard Decision**과 그 요청에 실제로 결속된 runtime facts를 immutable historical
authority로 저장·exact-read하는 경계를 정의한다.

이 계약은 Guide runtime entrypoint, Job/Outbox orchestration, Citation Source/Member Decision,
`CitationAuthorizationReceipt`, Citation finalizer, Release Gate, `PUBLIC_TRACK_F`를 포함하지 않는다.

## 2. Authority observation

`request_guard_decision_id`는 authoritative REQUEST evaluator가 제공하는 request-instance identity다.
Repository·reader는 이를 생성하거나 PASS로 추론하지 않는다. 같은 user와 operation의 반복 요청도 서로
다른 ID를 가져야 한다.

| 필드 | 규칙 |
| --- | --- |
| `request_guard_decision_id` | evaluator-issued UUID, immutable row identity |
| `actual_decision_outcome` | 실제 `PASS` 또는 `FAIL`, NULL/unknown 금지 |
| `user_id` / `request_operation_code` | evaluator observation과 기존 #713 Guard에 exact 결속 |
| `decision_stage` | `REQUEST` 고정 |
| `environment` | #810의 `LOCAL`, `TEST`, `CLOSED_DEMO`, `PRODUCTION` 중 exact value |
| `bundle_id` / `bundle_manifest_hash` | persisted Runtime Bundle의 exact 복합 identity |
| `request_scope_codes` | non-empty, unique, NFC, UTF-8 byte sorted tuple을 그대로 저장 |
| `scope_manifest_hash` | 기존 Citation kernel의 canonical scope hash와 exact 일치 |
| `legacy_request_authority_ref` | 기존 #713 Guard artifact identity에 대한 exact 복합 FK |

`request_scope_codes`는 hash에서 복원하지 않으며, missing/unknown 값은 PASS로 해석하지 않는다.
`FAIL` observation도 역사적 사실로 저장할 수 있지만, Citation binding assembly는 persisted PASS인
경우에만 허용한다.

## 3. Persistence and identity

물리 테이블은 `rag_request_guard_runtime_binding`이며 append-only다. writer는 caller가 소유한
transaction 안에서만 동작하고 commit하지 않는다.

`(artifact_code, artifact_version, artifact_content_sha256)`는 canonical semantic projection
`request-guard-runtime-binding-v1`에서 writer가 계산하는 immutable artifact identity다. artifact
version `1.0`과 projection version은 서로 다른 계약 필드다. 같은 request ID와 같은 semantic content의 재시도는
idempotent이며, 같은 request ID에 다른 content가 들어오면 conflict/fail closed한다. business
UPDATE/DELETE API와 DB trigger, RLS, stored procedure, UDF는 제공하지 않는다.

## 4. Read seam

AI Worker는 Backend ORM을 import하지 않는다. read-only adapter는 artifact identity 3열을 exact equality로
조회하고, persisted semantic facts에서 identity를 재계산해 요청 ref와 비교한다. `latest`, `CURRENT`,
`newest`, `ORDER BY created_at` fallback 및 부분 ref 조회는 금지한다.

정상적인 production read는 persisted observation만으로 기존 pure Citation kernel의
`RuntimeAuthorizationBinding`과 `OriginRequestGuardBinding`을 구성할 수 있어야 한다. 이 계약의 구현
자체가 production request decision emission, Citation authorization 완료, Guide runtime 연결을 의미하지는
않는다.

## 5. Database safeguards

Model과 migration은 다음을 함께 강제한다.

- outcome, stage, environment의 canonical vocabulary CHECK
- artifact/bundle/scope/legacy digest의 lowercase SHA-256 형식
- Runtime Bundle `(bundle_id, bundle_manifest_hash)` 복합 FK
- 기존 #713 Guard artifact identity 복합 FK
- append-only historical row를 보존하는 데이터 존재 시 downgrade 거부

Scope tuple의 NFC·정렬·hash 재계산은 shared pure contract와 Python writer가 검증한다. DB business
logic으로 대체하지 않는다. Runtime DB role은 이 표에 `SELECT, INSERT`만 가지며 `UPDATE`, `DELETE`,
`TRUNCATE`는 거부된다. Python write boundary도 #806 repository 한 곳으로 제한된다.

Current 승격 및 후속 runtime orchestration 연결은 별도 변경과 승인을 필요로 한다.
