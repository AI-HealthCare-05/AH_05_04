# 처방 확정 MVP Backend 계약

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
| `PRESCRIBED_DATE` | 필수, ISO 8601 날짜 형식(`YYYY-MM-DD`) |
| `MEDICATION_NAME` | 필수, 1~255자 |
| `DOSE_VALUE` | 필수, `NUMERIC(10,3)` 범위의 양수 |
| `FREQUENCY_PER_DAY` | 필수, `INTEGER(32비트)` 범위의 양수 정수 |
| `DURATION_DAYS` | 필수, `INTEGER(32비트)` 범위의 양수 정수 |
| `DOSE_UNIT` | 선택, 최대 50자 |
| `TIMING` | 선택, 최대 255자 |

`PRESCRIBED_DATE`가 없으면 `422 PRESCRIPTION_REQUIRED_FIELD_MISSING`, 값이 있지만 `YYYY-MM-DD` 형식이 아니면 `422 VALIDATION_FAILED`를 반환합니다. 다른 필수 필드가 누락되면 `422 PRESCRIPTION_REQUIRED_FIELD_MISSING`을 반환합니다. 형식, 범위, 길이가 맞지 않으면 `422 VALIDATION_FAILED`를 반환합니다.

## Post-MVP 이관

사용자가 검수한 OCR 작업과 확정 대상 OCR 작업을 `job_id`로 직접 일치 검증하는 기능은 Post-MVP 범위입니다.

Post-MVP에서는 처방 확정 요청에 `job_id`를 포함하고, Backend가 다음 조건을 검증하는 방향으로 확장합니다.

- 요청한 `job_id`가 해당 `document_id`의 OCR 작업인지 확인
- 요청 사용자가 해당 문서와 OCR 작업의 소유자인지 확인
- OCR 작업 상태가 `COMPLETED`인지 확인
- 확정 저장 시 동일한 OCR 작업 ID를 `prescription.source_ocr_job_id`로 저장
- OCR 재실행 후 다른 작업이 생성되어도 사용자가 검수한 작업이 바뀌지 않도록 보장
