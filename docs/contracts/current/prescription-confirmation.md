# 처방 확정 Backend 계약

## 목적

처방 확정 API가 OCR 검수 결과를 확정 처방 데이터로 저장할 때 사용하는 Backend 기준을 기록합니다.

## 현재 MVP 기준

- Endpoint: `POST /api/v1/documents/{document_id}/prescription`
- 요청 body는 사용하지 않습니다.
- Backend는 `document_id`의 소유권을 확인합니다.
- Backend는 문서에 연결된 최신 `COMPLETED` OCR 작업을 사용합니다.
- OCR 필드는 사용자가 확인한 `confirmed_value`만 처방 확정에 사용합니다.
- OCR 원문 `raw_value`와 정규화 참고값 `normalized_value`는 처방 확정값으로 자동 대체하지 않습니다.

## 필수 필드

`PRESCRIBED_DATE`는 처방 단위 필수 필드이며, Medication별 MVP 필수 필드는 다음과 같습니다.

| 필드 | 기준 |
| --- | --- |
| `PRESCRIBED_DATE` | 필수, `datetime.date.fromisoformat()`이 허용하는 ISO 8601 날짜 형식(예: `YYYY-MM-DD`, 하이픈 없는 `YYYYMMDD`, ISO week-date `YYYY-Www-D`) |
| `MEDICATION_NAME` | 필수, 1~255자 |
| `DOSE_VALUE` | 필수, `NUMERIC(10,3)` 범위의 양수 |
| `FREQUENCY_PER_DAY` | 필수, `INTEGER(32비트)` 범위의 양수 정수 |
| `DURATION_DAYS` | 필수, `INTEGER(32비트)` 범위의 양수 정수 |
| `DOSE_UNIT` | 선택, 최대 50자 |
| `TIMING` | 선택, 최대 255자 |
| `MEDICATION_STRENGTH` | 선택, 최대 100자. 제품 함량이며 `medication.strength_text`로 저장 |

`PRESCRIBED_DATE`가 없으면 `422 PRESCRIPTION_REQUIRED_FIELD_MISSING`, 값이 있지만 `date.fromisoformat()`이 파싱할 수 없는 형식이면 `422 VALIDATION_FAILED`를 반환합니다. 다른 필수 필드가 누락되면 `422 PRESCRIPTION_REQUIRED_FIELD_MISSING`을 반환합니다. 형식, 범위, 길이가 맞지 않으면 `422 VALIDATION_FAILED`를 반환합니다.

선택 필드는 검수 화면에 빈 입력란으로 표시될 수 있습니다.
값이 없는 선택 필드는 저장하지 않아도 처방 확정을 차단하지 않습니다.

OCR 값이 있는 선택 필드를 사용자가 제거한 경우에는
`PATCH /api/v1/extracted-fields/{field_id}`에
`confirmed_value: null`을 명시적으로 전송합니다.

Backend는 이 상태를 다음과 같이 저장합니다.

- `raw_value`: 기존 OCR 인식값 유지
- `confirmed_value`: `null`
- `confirmation_status`: `CONFIRMED`
- `confirmed_at`: 사용자 저장 시각

`MEDICATION_STRENGTH`, `DOSE_UNIT`, `TIMING`에만 이 상태를 허용하며,
필수 필드의 `confirmed_value: null`은 `422 VALIDATION_FAILED`로 거부합니다.

## 확정 이후 수정 금지

- 처방이 확정된 문서의 extracted-field는 더 이상 수정할 수 없습니다.
- 확정 이후 `PATCH /api/v1/extracted-fields/{field_id}` 요청은 `409 PRESCRIPTION_ALREADY_CONFIRMED`를 반환합니다.
- 거부된 PATCH는 기존 `confirmed_value`를 변경하지 않습니다.
- Frontend는 해당 오류를 받으면 편집을 중단하고 비편집 확정 상태로 전환합니다.
- PATCH와 처방 확정은 대상 `medical_document` row를 `SELECT ... FOR UPDATE`로 먼저 잠가 직렬화합니다.
- 잠금 대기가 3초를 초과하면 `409 CONCURRENT_UPDATE_IN_PROGRESS`를 반환하고 어떤 값도 변경하지 않습니다.
- 확정은 잠금 획득 이후에 읽은 검수값만 사용하므로, 확정 직전 commit된 PATCH는 반드시 확정 결과에 반영됩니다.
## Post-MVP 이관

사용자가 검수한 OCR 작업과 확정 대상 OCR 작업을 `job_id`로 직접 일치 검증하는 기능은 Post-MVP 범위입니다.

Post-MVP에서는 처방 확정 요청에 `job_id`를 포함하고, Backend가 다음 조건을 검증하는 방향으로 확장합니다.

- 요청한 `job_id`가 해당 `document_id`의 OCR 작업인지 확인
- 요청 사용자가 해당 문서와 OCR 작업의 소유자인지 확인
- OCR 작업 상태가 `COMPLETED`인지 확인
- 확정 저장 시 동일한 OCR 작업 ID를 `prescription.source_ocr_job_id`로 저장
- OCR 재실행 후 다른 작업이 생성되어도 사용자가 검수한 작업이 바뀌지 않도록 보장
