# RAG Source Governance Contract Receipt (#185)

| 항목 | 값 |
| --- | --- |
| Receipt | `rag-source-governance-contract-185@1.1.0` |
| 상태 | 최상위 `execution_status=NOT_IMPLEMENTED`, `decision_status=null` · 합성 계약 `COMPLETED/PASS` · 실제 Source readiness `BLOCKED` |
| 정본 JSON | [`tests/fixtures/rag/source_contract_receipt.json`](../../../tests/fixtures/rag/source_contract_receipt.json) |
| 계약 Target | [`rag-source-ingestion-v1.md`](../../contracts/targets/post-mvp-1/rag-source-ingestion-v1.md) |

## Authority

이 Receipt는 아래 Authority를 byte-level SHA-256으로 고정한다. 문서와 합성 테스트 통과는 실제 Source 구현 또는 운영 승인을 의미하지 않는다.

| Authority | Version / Decision | SHA-256 |
| --- | --- | --- |
| 외부 Manifest | `post-mvp-rag-evaluation-contract@2026-08-29.11` | `f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570` |
| Source Policy | `1.18` | `35842d2cbe54201ff9fb5580616055eda613fe4c16ac6d60daa7f8859d2f28e3` |
| DB Target | `1.47` | `f88ec11aaa6671184f2d0f5076219bf2ad51525b9e6a136ec5389afd2af82aea` |
| Local Source Target | `1` | `4342d0f772b6f63efe07d6323e95e62865a058caa8f8ae31bb844ad6cfba55b9` |
| Product Decision | `PD-125-20260831` | `f7e04cad41e3bc4c078e088906a9e7d50826f973fb8825fc45b23198a119ab85` |

## 선행 Receipt gate

`#155`의 Local Endpoint 검증은 [MFDS P0 Endpoint Receipt](./endpoints/README.md)와 세 JSON Receipt로 연결됐다. 제품 허가정보는 Endpoint parser gate를 통과했지만 DUR과 환자용 복약정보는 실제 Source의 불안정한 자연키로 인해 fail-closed 상태다. 또한 `#165` parser/source snapshot과 `#166` catalog 산출물의 Receipt·Interface가 아직 연결되지 않았으므로 전체 상태는 계속 `BLOCKED`이며 차단 코드는 `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`다. Issue Close 여부나 제품 Endpoint 단독 성공만으로 이 gate를 통과시키지 않는다.

| Operation stable code | Endpoint Receipt 상태 | Receipt hash |
| --- | --- | --- |
| `LIST_APPROVED_PRODUCTS` | `COMPLETED` / Endpoint parser gate 허용 | `1f96b97008a66d98b7a5e21c004ef7802c32a91787cfbcbe1abcf97d3f73e64c` |
| `LIST_INGREDIENT_CONTRAINDICATIONS` | `FAILED` / `SCHEMA_DRIFT` | `f8505cfafd24e428210f55ca9399fd9068a8195c911d4df8e6be24af055ccb4e` |
| `LIST_PATIENT_MEDICATION_GUIDES` | `FAILED` / `SCHEMA_DRIFT` | `4cd7e20483af4a815050f1d1bcf74dd3ef02423a5ff68d71a575ce035917b4bd` |

`#165`는 위 stable code와 Receipt hash를 함께 입력으로 고정해야 합니다. DUR과 환자용 복약정보의 자연키 정책이 해결되기 전에는 해당 차단 상태를 유지하며 Parser 또는 Snapshot 후보 등록을 활성화하지 않습니다. DUR의 `NOTIFICATION_DATE`가 후보 identity와 external version에 동시에 포함된 현재 구성도 확정 계약이 아니며, `#165`에서 두 역할을 분리할지 명시적으로 결정합니다.

실제 Service ID, Path, Primary Key, Response Schema, Content-Type, 성공 코드, Pagination, live DTO field는 이 Receipt에서 정하지 않는다. 외부 Policy와 로컬 Target의 bundle 요청 field(`runtime_release_bundle_id` 대 `bundle_id`)도 승인된 shared DTO Receipt 전까지 미확정이다.

## 적합성 계약

Source·Endpoint·Operation·Approval·License·재사용 조건·Attribution·Clinical Scope·Freshness·Revocation·Bundle membership를 모두 검사한다. 합성 Guard의 결과는 RAG-00과 같은 `PASS | FAIL`만 사용하며, 미승인과 만료는 각각 `APPROVAL_NOT_EFFECTIVE`, `APPROVAL_EXPIRED`로 분리한다. 불완전·회수·부분 Snapshot·Schema Drift도 독립 관측 축으로 보존하며 하나라도 부적격이면 fail-closed한다.

Machine Receipt는 Source hash/version/lifecycle, Endpoint·Operation 상태축, Snapshot version/checksum/parser/normalization/schema/verification, license·reuse·attribution, clinical scope, Approval purpose/effective/expiry, Freshness Policy와 검증 시각을 위한 typed slot을 모두 정의한다. 현재 실제 값은 #155/#165/#166 Receipt가 연결되지 않아 `NOT_CONNECTED/null`이다. 회수 뒤에는 신규 사용을 차단하되 과거 Identification·Citation provenance는 보존하고 현재 결과로 재사용하지 않는다.

실행형 합성 Guard 범위는 `REQUEST | CITATION_AUTHORIZATION`이다. 요청·활성 Bundle은 신뢰된 Target Bundle ID/Manifest와 모두 exact-match해야 하며 Target·Selection은 `ELIGIBILITY_TARGET | OPERATION_SELECTION`과 `RELEASE_SOURCE | SNAPSHOT_MEMBER`의 네 Canonical Envelope로 분리하여 Count/Hash/부분집합을 다시 계산한다. `member_kind`도 같은 `RELEASE_SOURCE | SNAPSHOT_MEMBER` enum만 사용한다. 두 Target과 실행 대상 Selection은 entry kind별 Count가 각각 1 이상이어야 한다. Target과 Selection 각각에서 모든 `SNAPSHOT_MEMBER`는 동일한 `source_code`, 불변 Snapshot 식별자인 `source_version`, 목적·승인·Scope/Freshness Policy 및 `bundle_build_source_verification_stable_key`를 가진 같은 집합의 `RELEASE_SOURCE`와 결속되어야 하며, 실행 대상 Selection의 모든 Entry는 요청 목적과 같은 `purpose_code`를 가져야 한다. 대응하는 `RELEASE_SOURCE` 없이 다른 Source 또는 다른 Snapshot의 Member만 교차 선택하는 조합은 차단한다. 이 합성 Guard는 같은 Source의 여러 적격 `RELEASE_SOURCE`와 각 Release에 결속된 Member를 한 Selection에 포함하는 것을 금지하는 Source별 단일 Snapshot cardinality를 정의하지 않는다. Envelope는 UTF-8·Unicode NFC·RFC 8785 JCS 규칙과 명시적 JSON `null`을 사용하고 중복 Entry를 거부한다. `RELEASE_SOURCE`의 Endpoint·Artifact 필드는 모두 null이어야 하며, `SNAPSHOT_MEMBER`는 Endpoint+Operation 또는 Artifact+Version 중 정확히 한 쌍만 가져야 한다. Citation은 별도 `PATIENT_CITATION` Approval과 원 `REQUEST/PASS` Guard의 Bundle ID·환경·Manifest·정렬 요청 Scope 목록·Scope Manifest Hash exact-match를 요구한다. 합성 Scope witness는 비어 있지 않은 중복 없는 NFC Scope Code를 UTF-8 byte 순으로 정렬한 compact JSON array의 SHA-256으로 재계산하며 Runtime wire schema 권한을 주장하지 않는다.

`EVALUATION_CANDIDATE | EVALUATION_REQUEST | PLANNED_ACTIVATION | EMERGENCY_ROLLBACK | RESUME`의 BUILDING candidate, protected runner, current/candidate 분리와 환경 전환은 정본 계약에만 기록된 후속 Runtime/Evaluation 경계다. 이 #185 evaluator는 필요한 전체 context가 없으므로 해당 Operation에 `PASS`를 반환하지 않고 `OPERATION_CONTEXT_NOT_MODELED`로 차단한다. 적격 rollback 후보가 없을 때 환경 결과는 `SUSPENDED`다.

## 목적별 적합성

모든 목적은 ACTIVE Source, 검증·활성·수집 승인 Endpoint/Operation, 유효하고 만료되지 않은 목적·환경별 Approval, 승인된 license·reuse·attribution, 허용 Clinical Scope, PUBLISHED/CURRENT Snapshot Verification, 미해결 Revocation 부재, Bundle Member exact-match를 공통으로 요구한다.

| 목적 | 허용 Source class | 별도 승인 |
| --- | --- | --- |
| `PRODUCT_IDENTIFICATION` | MFDS 제품 허가정보, 팀 승인 Alias | 필요 |
| `SAFETY_ROUTING` | MFDS DUR | 필요 |
| `RULE_DERIVATION` | MFDS DUR | 필요 |
| `RETRIEVAL` | MFDS 환자용 복약정보, 내부 검토 Guideline | 필요 |
| `PATIENT_CITATION` | MFDS 환자용 복약정보, 내부 검토 Guideline | 필요; `RETRIEVAL` 승인으로 대체 불가 |

## Resolver 경계

허용 입력은 사용자 확정 `medication_name`, nullable `strength_text`, 그리고 연결된 Receipt가 증명하는 승인 MFDS Catalog provenance뿐이다. OCR `raw_value`, 검수 전 LLM Structured Output, HIRA 데이터, 승인되지 않은 Source, 의료 Evidence Index를 Candidate 원장으로 사용하는 것은 금지한다. Candidate Index와 Evidence Index는 별도 version·물리 경계를 가지며 Candidate vector 결과를 의료 Citation으로 사용하지 않는다.

## 공개·활성화

`EXT-MED-002`, `EXT-PHARM-001`, `EXT-SOURCE-001`, `EXT-SOURCE-002`, `EXT-PRIV-001`, `EXT-PRIV-002`, `EXT-SAFETY-001`이 모두 충족되기 전에는 `PUBLIC_TRACK_F=false`다. 실제 사용자 Source 활성화와 Production Runtime Bundle 편입은 금지하며, 합성 fixture를 이용한 통제된 Local demo만 허용한다. 해제 판단의 단일 정본은 [Post-MVP-1 외부 승인](../../release-gates/post-mvp-1-external-approvals.md)이다.

## Receipt 무결성과 후속 입력

Canonical Receipt hash는 `sha256:d687e75ebfbb3bc10b9887280e5c994bcc7bc0481722c660c6ce2cda8c3d402a`다. 이 합성 Receipt는 #167, #168, #170, #175의 입력 선행조건으로 연결되지만 실제 Source binding이 채워진 새 Receipt 전까지 네 Issue 모두 `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`다.

## 사용 범위

이 Receipt는 계약·테스트 입력 경계를 고정하는 합성 증빙이다. 실제 Source Snapshot, Catalog, DB migration, API, Resolver, Runtime activation 또는 외부 승인 증빙으로 해석하지 않는다.
