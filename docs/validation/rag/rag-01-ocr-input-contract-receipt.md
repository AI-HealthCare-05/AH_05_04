# RAG-01 OCR 확정 입력 Contract Receipt

| 항목 | 값 |
| --- | --- |
| Receipt version | `2.0` |
| Canonical Receipt hash | `sha256:06aeb8e201194fae2caf77fec699c104f5038109329d731c807810ac5b4fed12` |
| 검증 상태 | `COMPLETED` |
| 검증일 | 2026-09-09 |
| 검증 기준 Commit | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` |
| 추적 Issue | `#154`, `#169` |

> 이 Receipt는 활성 Prescription Version Medication이 Resolver에 제공할 수 있는 OCR 확정 입력임을 증명한다. RAG-08·RAG-09 전체 구현 완료 또는 Track F 공개 승인을 의미하지 않는다.

## Canonical Receipt hash

기계 판독 정본은 `docs/validation/rag/rag-01-ocr-input-contract-receipt.json`이며 hash는 다음 명령으로 재생성·검증한다.

```bash
uv run python scripts/verify_rag_01_receipt.py \
  docs/validation/rag/rag-01-ocr-input-contract-receipt.json --write
uv run python scripts/verify_rag_01_receipt.py \
  docs/validation/rag/rag-01-ocr-input-contract-receipt.json
```

Canonicalization은 `generated_at`과 `receipt_hash`를 제외하고 key 정렬·compact UTF-8 JSON에 SHA-256을 적용한다.

## 1. 확정 입력 경계

처방 확정은 `extracted_field.confirmed_value`만 사용한다. `raw_value`, `normalized_value`, 검수 전 OCR 결과 또는 LLM draft로 대체하지 않는다. 신규 확정 transaction은 다음 Version-only graph를 생성한다.

- `prescription.active_version_id`
- `prescription_version`
- `prescription_version_medication`

확정 이후 runtime은 legacy `medication` row를 쓰거나 읽지 않는다. `medication_name`은 필수 문자열이고 `strength_text`는 복합제·농도 표현을 보존하는 nullable 문자열이다.

## 2. Commit·line-level 증빙

아래 표는 JSON Receipt의 `current_runtime_model.source_locations`와 동일하다.

| 파일 | 줄 | Commit SHA | 증빙 유형 | 검증 의미 |
| --- | ---: | --- | --- | --- |
| `backend/app/services/prescriptions.py` | 27-36 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `SOURCE_INSPECTION` | confirmed_value만 읽고 raw_value 또는 normalized_value로 대체하지 않는다. |
| `backend/app/services/prescriptions.py` | 80-138 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `SOURCE_INSPECTION` | 소유권과 문서 잠금을 확인한 뒤 Version 1 snapshot을 생성하고 실제 Version ID로 응답한다. |
| `backend/app/repositories/prescription_repository.py` | 58-105 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `SOURCE_INSPECTION` | 신규 확정은 Prescription, PrescriptionVersion, PrescriptionVersionMedication만 쓰고 legacy medication을 쓰지 않는다. |
| `backend/app/models/prescriptions.py` | 40-99 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `SOURCE_INSPECTION` | 필수 active_version_id와 같은 Prescription에 속한 Version만 허용하는 composite FK를 정의한다. |
| `backend/app/models/prescriptions.py` | 153-245 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `SOURCE_INSPECTION` | 불변 PrescriptionVersion과 nullable 문자열 strength_text를 포함한 Medication snapshot을 정의한다. |
| `backend/app/dtos/prescriptions.py` | 14-38 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `SOURCE_INSPECTION` | 응답에서 prescription_version_id와 prescription_version_medication_id를 필수로 공개한다. |
| `backend/alembic/versions/169a1b2c3d4e_add_prescription_version_schema.py` | 1-260 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `MIGRATION` | Prescription Version schema, snapshot 무결성 제약과 불변성 trigger를 추가한다. |
| `backend/alembic/versions/169b2c3d4e5f_backfill_prescription_versions.py` | 1-260 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `MIGRATION` | legacy 확정 처방을 Version 1 snapshot으로 검증·이관한다. |
| `backend/alembic/versions/169c3d4e5f6a_cut_over_prescription_version_reads.py` | 1-260 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `MIGRATION` | 하위 참조를 Version provenance로 전환하고 legacy downgrade 위험을 차단한다. |
| `backend/alembic/versions/169d4e5f6a7b_harden_prescription_version_links.py` | 1-61 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `MIGRATION` | 활성 Version과 Guide·Chat Version 링크를 NOT NULL로 고정한다. |
| `tests/migration/test_prescription_version_backfill_migration.py` | 742-850 | `ee277a81c035302a2fa61dbe3b4d94613c2f35b3` | `REGRESSION_TEST` | PostgreSQL에서 Version-only 쓰기, 정정 경쟁, cutover와 hardening을 검증한다. |

Migration chain `169a1b2c3d4e → 169b2c3d4e5f → 169c3d4e5f6a → 169d4e5f6a7b`는 PostgreSQL 17에서 `upgrade head`와 migration 회귀 테스트로 검증한다.

## 3. Resolver allowlist

| 필드 | nullable | 허용 조건 |
| --- | ---: | --- |
| `prescription_version_id` | 아니요 | 요청이 고정한 활성 Prescription Version |
| `prescription_version_medication_id` | 아니요 | 해당 Version에 속한 불변 Medication snapshot |
| `medication_name` | 아니요 | 사용자가 명시적으로 확정한 활성 Version Medication 값 |
| `strength_text` | 예 | 같은 활성 Version Medication에 저장된 사용자 확정 문자열 |

`strength_text=null`은 정상 입력이며 제품명 경로로 검색한다.

## 4. 금지 입력

- `extracted_field.raw_value`, `extracted_field.normalized_value`
- 검수 전 OCR 구조화 결과, LLM draft
- `source_ids`, `insurance_code_text`
- 확정하지 않은 수정값
- 비활성·과거 Prescription Version Medication
- legacy `medication` row
- 실제 환자정보 또는 의료문서 원문

## 5. 해소된 Gap

| 코드 | 판정 | 근거 |
| --- | --- | --- |
| `PRESCRIPTION_VERSION_NOT_IMPLEMENTED` | `RESOLVED` | Version schema, Version 1 backfill, read cutover, Version-only writer·reader와 hardening 구현 |
| `TARGET_STRENGTH_MAPPING_NOT_FROZEN` | `RESOLVED` | `prescription_version_medication.strength_text` nullable 문자열로 고정 |

## 6. 테스트와 후속 Gate

OCR 확인 흐름, Receipt contract, PostgreSQL migration 회귀 테스트를 실행한다. 합성 데이터만 사용하며 실제 환자·처방·의료문서를 사용하지 않는다.

Prescription Version 입력 선행조건은 `READY=true`다. 그러나 이 판정은 #170의 Source·Index·Resolver 선행조건이나 #171의 정책·멱등성·평가 선행조건을 자동으로 해소하지 않는다.

```text
PRESCRIPTION_VERSION_INPUT READY = true
RAG-08/09 OVERALL READY = false
RAG-08 BLOCKED_BY = BLOCKED_BY_RAG_08_PREREQUISITE
RAG-09 BLOCKED_BY = BLOCKED_BY_RAG_09_PREREQUISITE
PUBLIC_TRACK_F = false
```

## 7. 결론

사용자가 확정한 OCR 입력은 현재 활성 불변 Prescription Version Medication에 저장되며 실제 Version ID와 Medication Snapshot ID로 추적된다. 따라서 RAG-01의 Prescription Version 입력 gap은 해소됐다. 외부 Source·Privacy·의료·약학·Safety 승인과 각 후속 Issue의 별도 구현·평가가 완료되기 전에는 RAG 전체 준비 또는 Production 공개 근거로 사용하지 않는다.
