# RAG-01 OCR 확정 입력 Contract Receipt

| 항목 | 값 |
| --- | --- |
| Receipt version | `1.0` |
| Canonical Receipt hash | `sha256:e134ad8ff700050456d5d77976336b61a795207cafbe12f385cb7c9bba2c92fe` |
| 검증 상태 | `COMPLETED_WITH_GAPS` |
| 검증일 | 2026-09-03 |
| 검증 기준 Commit | `723758ec361a29e97a256ac45ae7a17d8b0dae50` |
| PR #96 Merge Commit | `e82f782fa09e068d43d7e78c028f412620e1d0c5` |
| 추적 Issue | `#154` |
| 구현 담당 | 김지혜 (`@Jye-rookie`) |
| Backend·DB 리뷰 | 송은영 (`@phina-io`) |
| RAG 소비 경계 리뷰 | 정현우 (`@ceohwj`) |

> 실제 환자 정보, 의료문서, API Key 또는 Provider credential을 이 문서와 테스트 증빙에 포함하지 않는다.

## Canonical Receipt hash

이 Receipt의 기계 판독 정본 hash는 다음과 같다.

```text
sha256:e134ad8ff700050456d5d77976336b61a795207cafbe12f385cb7c9bba2c92fe
```
Canonicalization 규칙:
1. 최상위 generated_at과 receipt_hash 필드를 제외한다.
2. JSON object key를 오름차순으로 정렬한다.
3. 불필요한 공백 없이 compact JSON으로 직렬화한다.
4. 문자열은 UTF-8로 인코딩하며 ASCII escape를 강제하지 않는다.
5. 직렬화된 bytes에 SHA-256을 적용한다.

규칙 식별자는 다음과 같다.

```text
sorted-keys-compact-utf8-excluding-generated_at-and-receipt_hash-v1
```
따라서 같은 commit·schema·의미를 가진 Receipt는 generated_at이 달라져도 같은 hash를 생성한다. 그 밖의 의미 있는 필드가 변경되면 hash를 다시 생성하고 모든 선행조건 문서의 참조값을 함께 갱신해야 한다.
재생성 및 검증 명령:

```bash
uv run python scripts/verify_rag_01_receipt.py \
  docs/validation/rag/rag-01-ocr-input-contract-receipt.json \
  --write

uv run python scripts/verify_rag_01_receipt.py \
  docs/validation/rag/rag-01-ocr-input-contract-receipt.json
```

## 1. 목적

PR #96에서 구현된 OCR 검수·처방 확정 흐름이 RAG Candidate Resolver에 전달할 수 있는 입력 경계를 확인한다.

이번 Receipt는 현재 Runtime과 목표 Prescription Version 구조를 구분하여 다음 사항을 고정한다.

- 현재 처방 확정 흐름이 OCR `raw_value`나 `normalized_value`를 직접 사용하지 않는지 확인
- 사용자 `confirmed_value`가 현재 `medication.medication_name`과 nullable `medication.strength_text`로 저장되는지 확인
- 사용자 확정 전 값이 Resolver 입력으로 사용되지 않도록 금지 입력을 명시
- 현재 Runtime과 목표 불변 Prescription Version Medication 사이의 구현 차이를 기록
- #101에서 구현된 PATCH·확정 직렬화 경계를 연결
- RAG-08·RAG-09 착수 가능 여부를 명시

이 Receipt는 RAG 또는 MFDS 기능의 구현·공개 승인을 의미하지 않는다.

### 확장 회귀 테스트

실행 명령:

```bash
uv run pytest \
  backend/app/tests/ocr \
  backend/app/tests/ocr_ai \
  backend/app/tests/repositories \
  tests/contract \
  -q
  ```

실행 결과 :

`306 passed`

## 2. 기준 계약

### 2.1 Product/Governance Decision

- `docs/governance/decisions/2026-08-31-rag-p0-contract-freeze.md`
- Decision ID: `PD-125-20260831`
- 상태: `Approved Target · Not implemented`

### 2.2 처방 버전 계약

- `docs/contracts/targets/post-mvp-1/prescription-version-v1.md`
- 목표 구조:
  - `prescription`
  - `prescription_version`
  - `prescription_version_medication`
  - `prescription.active_version_id`
- 사용자가 명시적으로 확정한 활성 불변 Medication Snapshot만 하위 RAG 입력으로 사용할 수 있다.

### 2.3 의약품 식별 계약

- `docs/contracts/targets/post-mvp-1/medication-identification-v1.md`

Issue #154에 기재된 다음 경로는 현재 존재하지 않는다.

```text
docs/contracts/proposed/post-mvp-1/medication-candidate-identification-v1.md
```

해당 논리 계약은 `medication-identification-v1.md`에 통합되었으며 별도의 중복 Target 파일을 만들지 않는다.

## 3. PR #96 반영 확인

PR #96 Merge Commit은 다음과 같다.

```text
e82f782fa09e068d43d7e78c028f412620e1d0c5
```

검증 기준 Commit은 다음과 같다.

```text
723758ec361a29e97a256ac45ae7a17d8b0dae50
```

PR #96 Merge Commit은 검증 기준 Commit의 ancestor이므로 현재 `develop`에 포함되어 있다.

PR #96에서 추가된 `medication.strength_text` Migration은 다음 파일에서 확인한다.

```text
backend/alembic/versions/529b2a36b677_add_medication_strength_and_ocr_prompt_.py
```
### 3.1 Commit·line-level 증빙

현재 Runtime 감사의 기준 Commit은 `723758ec361a29e97a256ac45ae7a17d8b0dae50`이다.

| 영역 | 파일 | 줄 | 검증 방식 | 확인 내용 |
| --- | --- | ---: | --- | --- |
| API router | `backend/app/apis/v1/medical_document_routers.py` | 78-94 | Source inspection | 처방 확정 API가 `PrescriptionService`를 호출하고 `PrescriptionResponse`를 201로 반환 |
| 확정값 선택 | `backend/app/services/prescriptions.py` | 27-36 | Source inspection | `confirmed_value`만 사용하고 `raw_value`·`normalized_value` fallback 금지 |
| 확정 transaction | `backend/app/services/prescriptions.py` | 72-125 | Source inspection | 소유권·row lock·중복 확정 검사 후 Prescription과 Medication 생성 |
| 현재 Prescription | `backend/app/models/prescriptions.py` | 39-85 | Source inspection | 현재 저장 구조와 `profile_id` 소유권 경계 |
| 현재 Medication | `backend/app/models/prescriptions.py` | 88-130 | Source inspection | `medication_name`과 nullable 문자열 `strength_text` |
| 저장 Repository | `backend/app/repositories/prescription_repository.py` | 33-55 | Source inspection | Prescription과 Medication을 동일 DB session에서 생성하고 flush |
| 공개 DTO | `backend/app/dtos/prescriptions.py` | 13-33 | Source inspection | `medication_name: str`, `strength_text: str \| None` |
| PR #96 Migration | `backend/alembic/versions/529b2a36b677_add_medication_strength_and_ocr_prompt_.py` | 22-126 | Source inspection | nullable `strength_text`, `MEDICATION_STRENGTH` 제약 및 downgrade 안전 가드 |
| 정상 확정 회귀 | `backend/app/tests/ocr/test_prescription_confirmation_api.py` | 117-130 | Regression test | 사용자 확정 필드 기반 Medication 생성 |
| 소유권 회귀 | `backend/app/tests/ocr/test_prescription_confirmation_api.py` | 254-267 | Regression test | 타 사용자 의료문서 처방 확정 404 |
| nullable 함량 회귀 | `backend/app/tests/ocr/test_prescription_confirmation_api.py` | 270-337 | Regression test | 확정된 선택 함량이 `strength_text=NULL`로 보존 |
| 동시성 회귀 | `backend/app/tests/ocr/test_prescription_confirmation_concurrency.py` | 215-316 | Regression test | PATCH·확정 직렬화와 동시 확정 단일 성공 |

### 3.2 Migration 검증 수준

PR #96 Migration은 코드 확인과 실제 PostgreSQL 실행을 구분해 기록한다.

| 검증 | 결과 | 증빙 |
| --- | --- | --- |
| Migration source inspection | PASS | 기준 Commit `723758ec361a29e97a256ac45ae7a17d8b0dae50`, revision `529b2a36b677`, lines 22-126 |
| PostgreSQL Alembic 실행 | PASS | PR #243 commit `ac06ae9f88649455d26a154a1050cd449396af3f`의 `Run PostgreSQL migrations` |
| 실행 명령 | PASS | `uv run alembic -c backend/alembic.ini upgrade head` |

따라서 Migration 존재 여부만 정적으로 확인한 것이 아니라 PostgreSQL에서 Alembic migration 적용이 성공했음을 별도 증빙으로 기록한다.

## 4. 현재 Runtime 감사

현재 Runtime은 목표 Prescription Version 구조가 아니라 `prescription`과 `medication`을 직접 생성한다.

### 4.1 확정값 선택

`backend/app/services/prescriptions.py:27`의 `_field_value()`는 `ExtractedField.confirmed_value`만 읽는다.

다음 값은 처방 확정 입력으로 대체 사용하지 않는다.

- `raw_value`
- `normalized_value`
- 검수 전 OCR 구조화 결과
- LLM draft

`confirmed_value`가 없거나 공백뿐이면 값이 없는 것으로 처리한다.

### 4.2 처방 확정과 소유권

`backend/app/services/prescriptions.py:72`의 `confirm_prescription()`은 다음 순서로 처리한다.

1. `medical_document` row를 소유권 조건과 함께 잠근다.
2. 타 사용자 문서는 `404 MEDICAL_DOCUMENT_NOT_FOUND`로 처리한다.
3. 이미 확정된 문서는 `409 PRESCRIPTION_ALREADY_CONFIRMED`로 처리한다.
4. 최신 `COMPLETED` OCR Job을 조회한다.
5. 사용자 확정 필드로 처방 데이터를 구성한다.
6. 현재 `prescription`과 `medication` row를 생성한다.

소유권 정본은 `medical_document.profile_id`에서 파생된 `prescription.profile_id`다.

### 4.3 현재 저장 구조

| 입력 | 현재 저장 위치 | nullable | 현재 상태 |
| --- | --- | ---: | --- |
| 확정 약명 | `medication.medication_name` | 아니요 | 구현됨 |
| 확정 제품 함량 | `medication.strength_text` | 예 | 구현됨 |
| 처방 확정 시각 | `prescription.confirmed_at` | 아니요 | 구현됨 |
| OCR 출처 | `prescription.source_ocr_job_id` | 아니요 | 구현됨 |
| 사용자 소유권 | `prescription.profile_id` | 아니요 | 구현됨 |

`strength_text`는 복합제와 농도 표현을 보존하기 위해 숫자형으로 변환하지 않고 최대 100자의 문자열로 저장한다.

### 4.4 현재 공개 DTO

`backend/app/dtos/prescriptions.py`의 `MedicationData`는 다음 필드를 공개한다.

- `medication_name: str`
- `strength_text: str | None`

현재 DTO는 Prescription Version Medication ID를 제공하지 않는다.

## 5. 현재 구조와 목표 구조 비교

| 목표 입력 | 현재 저장 위치 | 목표 저장 위치 | 현재 상태 | 차단 코드 |
| --- | --- | --- | --- | --- |
| 확정 약명 | `medication.medication_name` | `prescription_version_medication.medication_name` | 현재 구현, 목표 미구현 | `PRESCRIPTION_VERSION_NOT_IMPLEMENTED` |
| nullable 제품 함량 | `medication.strength_text` | 목표 Medication Snapshot의 승인 컬럼 | 현재 구현, 목표 물리 mapping 미확정 | `TARGET_STRENGTH_MAPPING_NOT_FROZEN` |
| 사용자 확정 provenance | `extracted_field.confirmed_value`에서 Prescription 생성 | 불변 Prescription Version provenance | 부분 구현 | `PRESCRIPTION_VERSION_NOT_IMPLEMENTED` |
| 활성 Snapshot | 현재 별도 구조 없음 | `prescription.active_version_id`가 가리키는 불변 version | 미구현 | `PRESCRIPTION_VERSION_NOT_IMPLEMENTED` |
| Medication Snapshot ID | 현재 `medication.id` | `prescription_version_medication.id` | 미구현 | `PRESCRIPTION_VERSION_NOT_IMPLEMENTED` |

현재 `medication` row가 존재하더라도 이를 목표 활성 Prescription Version Medication과 동일한 것으로 간주하지 않는다.

## 6. Resolver 입력 Allowlist

Candidate Resolver는 다음 조건을 모두 만족하는 값만 입력으로 받을 수 있다.

| 필드 | nullable | 허용 조건 |
| --- | ---: | --- |
| `medication_name` | 아니요 | 사용자가 명시적으로 확정하고 활성 불변 Prescription Version Medication에 저장된 값 |
| `strength_text` | 예 | 같은 활성 불변 Medication Snapshot에 저장된 값 |

`strength_text=null`은 정상 입력이다. 이 경우 제품명 경로로 검색해야 한다.

현재 Runtime에는 활성 Prescription Version Medication이 없으므로 이 Allowlist를 충족하는 목표 Resolver 입력은 아직 제공할 수 없다.

## 7. Resolver 금지 입력

다음 값은 Candidate Resolver에 직접 전달하면 안 된다.

- `extracted_field.raw_value`
- `extracted_field.normalized_value`
- 검수 전 OCR 구조화 결과
- LLM draft
- `source_ids`
- `insurance_code_text`
- 사용자가 수정만 하고 처방 확정을 완료하지 않은 값
- 비활성 또는 과거 Prescription Version Medication
- 이름·함량·배열 순서로 추정 연결한 과거 Medication
- 실제 환자정보 또는 의료문서 원문

보험코드는 별도의 OCR·Prescription 공유 계약, Migration, 승인 Source와 Contract Test가 확정되기 전까지 비활성이다.

## 8. 동시 수정·확정 경계

선행 Issue #101은 완료 상태다.

현재 구현은 `medical_document` row lock을 이용해 다음 요청을 직렬화한다.

- extracted-field PATCH
- 처방 확정
- 동시 처방 확정

확정이 완료된 후의 extracted-field PATCH는 `409 PRESCRIPTION_ALREADY_CONFIRMED`로 거부되며 기존 `confirmed_value`를 변경하지 않는다.

관련 증빙:

```text
backend/app/tests/ocr/test_prescription_confirmation_concurrency.py
backend/app/tests/ocr/test_prescription_confirmation_api.py
```

이 경계는 현재 Prescription 생성 흐름을 보호한다. 목표 Prescription Version 생성·활성화의 `base_version_id` 및 `active_version_id` 경쟁 제어는 아직 구현되지 않았다.

## 9. 테스트 증빙

실행 명령:

```bash
uv run pytest \
  backend/app/tests/ocr/test_prescription_confirmation_api.py \
  backend/app/tests/ocr/test_prescription_confirmation_validation.py \
  backend/app/tests/ocr/test_prescription_confirmation_concurrency.py \
  -q
```

실행 결과:

```text
49 passed
```

검증된 동작:

- 사용자 `confirmed_value` 기반 처방 생성
- 미검수 필드가 있는 경우 처방 생성 차단
- nullable `strength_text` 보존
- 문자열 `strength_text` 보존
- 타 사용자 문서 접근 시 404
- 처방 확정 이후 PATCH 차단
- 동시 PATCH·확정 직렬화
- 동시 확정 요청에서 단일 성공 경계
- 복합 함량 문자열을 숫자로 변환하지 않고 그대로 보존
- `confirmed_value`가 없을 때 `raw_value` 또는 `normalized_value`로 대체하지 않음

테스트는 합성 데이터만 사용하며 실제 환자·처방·의료문서를 사용하지 않는다.

### 정적 검사와 전체 CI

| 검증 | 결과 |
| --- | --- |
| `uv run ruff check .` | PASS |
| `uv run ruff format . --check` | PASS |
| `uv run mypy backend/app ai_worker` | PASS |
| `bash scripts/ci/run_test.sh` | PASS |

## 10. 미해결 Gap

### 10.1 `PRESCRIPTION_VERSION_NOT_IMPLEMENTED`

현재 Runtime에는 다음 구조가 없다.

- `prescription_version`
- `prescription_version_medication`
- `prescription.active_version_id`
- 불변 Medication Snapshot
- 활성 Version 변경과 과거 Version 보존
- Prescription Version 기반 하위 데이터 귀속

따라서 현재 `medication` 값을 목표 Resolver 입력으로 바로 승격할 수 없다.

### 10.2 `TARGET_STRENGTH_MAPPING_NOT_FROZEN`

논리 계약은 nullable `strength_text`를 요구하지만 목표 `prescription_version_medication`의 물리 컬럼과 Migration mapping은 아직 구현 PR에서 확정되지 않았다.

물리 구조를 현재 `medication.strength_text`와 동일하다고 추정하지 않는다.

## 11. 후속 작업 Gate

다음 조건이 충족되기 전에는 RAG-08·RAG-09를 착수 가능 또는 완료로 표시하지 않는다.

1. Prescription Version 모델과 Migration 구현
2. 기존 확정 Medication의 Version 1 Snapshot 이관
3. `prescription.active_version_id` 활성 포인터 구현
4. 사용자 확정 transaction에서 불변 Medication Snapshot 생성
5. nullable `strength_text` 물리 mapping 확정
6. 소유권·동시성·현재성 Contract Test 통과
7. 이 Receipt 재생성 및 미해결 Gap 해소 확인

현재 판정:

```text
RAG-08/09 READY = false
BLOCKED_BY = PRESCRIPTION_VERSION_NOT_IMPLEMENTED
```

## 12. 개인정보·보안·의료 안전

- 실제 환자·처방·진료 데이터를 사용하지 않았다.
- 의료문서 또는 OCR 원문을 Receipt에 포함하지 않았다.
- API Key, Authorization Header 또는 Provider credential을 포함하지 않았다.
- OCR `raw_value`와 `normalized_value`를 사용자 확정 입력으로 승격하지 않았다.
- 미확정값을 Resolver 또는 RAG 입력으로 허용하지 않았다.
- 이 Receipt를 RAG Production 활성화 승인으로 해석하지 않는다.
- 외부 Source·Privacy·의료·약학·Safety 승인 전에는 `PUBLIC_TRACK_F=false`를 유지한다.

## 13. 결론

PR #96의 현재 Runtime은 다음 경계를 충족한다.

- 처방 확정 시 `confirmed_value`만 사용
- 확정 약명을 `medication.medication_name`에 저장
- 제품 함량을 nullable 문자열 `medication.strength_text`로 저장
- 미검수 입력 차단
- 소유권 404
- PATCH·확정 직렬화

그러나 목표 활성 불변 Prescription Version Medication은 아직 구현되지 않았다.

따라서 현재 Runtime 감사는 완료했지만 RAG Candidate Resolver의 목표 입력 준비 상태는 완료가 아니다. 후속 구현은 `PRESCRIPTION_VERSION_NOT_IMPLEMENTED`와 `TARGET_STRENGTH_MAPPING_NOT_FROZEN`을 해소한 뒤 진행한다.
