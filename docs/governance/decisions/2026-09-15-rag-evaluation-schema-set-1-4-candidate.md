# Product Decision Candidate: RAG Evaluation Schema Set 1.4

| 항목 | 값 |
| --- | --- |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-15 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 김지혜 (`@Jye-rookie`) — Evaluation·Source provenance·Safety fixture 계약 |
| 추적 Issue | [#160](https://github.com/AI-HealthCare-05/AH_05_04/issues/160) · [#161](https://github.com/AI-HealthCare-05/AH_05_04/issues/161) |
| 적용 범위 | Post-MVP-1 Track F Grounding·Citation·Safety Evaluation projection 계약 후보 |

## 후보 결정

`rag-eval.schema-set@1.4.0`을 #160 Grounding·Citation observation과 #161 Safety critical union의
same-Case signal 입력에 필요한 최소 후보 Schema Set으로 제안한다. 이 후보의 승인 전환에는 책임 리뷰어
김지혜 (`@Jye-rookie`)의 실제 Pull Request review event가 필요하며, schema와 문서의 존재만으로 그 event를
대신할 수 없다.

| Immutable field | 값 |
| --- | --- |
| Schema Set ID | `rag-eval.schema-set` |
| Schema Set version | `1.4.0` |
| Schema Set SHA-256 | `13cb59316be25c80ecaad2e3ae87bff6d0a4f1ebfb5f75c888ff3c7ae85a0a8c` |
| Canonical member root | `evals/schemas/1.4.0/` |
| Member count | `23` |

Schema Set hash는 member별 `{schema_id, schema_version, schema_sha256}`를 정렬한 canonical JSON의
SHA-256이다. Set version과 member version은 독립적으로 검증한다.

## Member versioning

Schema Set `1.3.0`의 21개 member와 각 canonical bytes를 그대로 재사용하고 다음 두 member를
`1.0.0`으로 추가한다.

- `rag-eval.claim-citation-observation@1.0.0`
- `rag-eval.grounding-signal@1.0.0`

따라서 전체 member는 경로와 schema ID가 각각 고유한 23개다. 기본 exporter version은 계속 `1.0.0`이며,
기존 `evals/schemas/1.0.0/`부터 `1.3.0/`까지의 bytes와 hash를 변경하지 않는다.

## Projection 계약

Claim–Citation observation은 completed `ANSWER_GROUNDING | SAFETY | END_TO_END_RAG` Case의 Run·Case·Dataset·
input·answer·Answer variant에 결속하며 Claim과 Citation edge, #180 validation·authorization 결과, Gold Source
일치 여부를 stable ID·bounded enum·immutable reference·hash로만 보존한다. Claim/Citation/Source/Answer 원문과
Provider payload는 저장하지 않는다.

Citation의 `source_version`은 #180과 동일한 bounded opaque NFC token을 그대로 보존한다. authorization은
validation의 후속 단계이므로 validation `REJECTED`에서는 envelope와 edge에 명시적인 not-run 상태를 기록하고,
validated selection이 있을 때만 authorization decision·receipt를 허용한다. Citation key는 observation 전체의
flattened 순서에서 UTF-16 정렬돼야 한다.

Grounding signal은 `SAFETY | END_TO_END_RAG` Case에 결속하며 `EVALUATED | NOT_APPLICABLE_NO_CLAIMS`를 구분한다.
no-claims 상태는 generation 미실행·폐기의 `answer_sha256=null`과 승인 fallback의 non-null answer hash를 모두
허용하지만 observation reference/hash는 null이고 세 failure boolean은 모두 false여야 한다.

Python strict model과 exported Draft 2020-12 schema는 task enum, validation-before-authorization 인과와 receipt tuple,
criticality judgment reference, no-claims state를 fail-closed한다. 정렬·중복·orphan과 canonical self-hash는
Python parser가 추가 검증한다. portable 조건 검증은 required dev dependency인 `jsonschema`로 실행한다.
Run·Case Result·Gold·#180 receipt 사이의 실제 외부 artifact exact matching은
후속 pure projection builder 입력 검증의 책임이다.

## 적용 경계

이 후보는 두 projection model, canonical JSON Schema export, registry member와 schema-set hash만 소유한다.
다음은 포함하지 않는다.

- #160 Grounding·Citation metric kernel 또는 #161 Safety·Rule-first metric kernel
- #180 Runtime dataclass를 Evaluation 정본으로 직접 사용하는 결합
- Runtime adapter·Provider·DB·network 연결
- frozen HOLDOUT 또는 SAFETY_REGRESSION 관찰·실행
- Baseline Freeze, active threshold, Release `PASS | FAIL`, `PUBLIC_TRACK_F`
- #159 human judgment 또는 3-pair Answer comparison schema

책임 리뷰어의 실제 승인 전에는 Schema Set `1.4.0`을 Approved 입력으로 취급하거나 #160·#161 metric
구현 선행조건이 완료됐다고 기록하지 않는다.
