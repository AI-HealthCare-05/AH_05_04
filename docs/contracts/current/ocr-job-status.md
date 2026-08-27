# OCR 작업 상태 조회 Backend 계약

## 목적

OCR 작업 실행·조회 API가 성공·실패 상태를 Frontend에 전달할 때 사용하는 필드와 오류 코드 기준을 기록합니다.

## 현재 MVP 기준

- Endpoint: `POST /api/v1/documents/{document_id}/ocr-jobs`, `GET /api/v1/ocr-jobs/{job_id}`
- 응답 `data`에는 `error_code`, `error_message`를 포함해 실패 상태를 화면에서 안내할 수 있도록 합니다.
- `error_message`는 외부 OCR 제공자의 원본 오류나 민감한 내부 예외 메시지를 그대로 노출하지 않고, Backend가 정의한 고정된 안전한 문구만 반환합니다.
- 문서에 연결된 OCR 작업이 여러 개이고 `created_at`이 동일한 경우, `created_sequence` 컬럼으로 최신 작업을 안정적으로 판별합니다.

## 실패 코드

| `error_code` | 상황 | HTTP status |
| --- | --- | ---: |
| `OCR_PROVIDER_TIMEOUT` | OCR 제공자 응답 시간 초과 | `503` |
| `OCR_PROVIDER_CALL_FAILED` | OCR 제공자 연결 실패 | `503` |
| `OCR_PROVIDER_UNAVAILABLE` | OCR 제공자 일시적 사용 불가 | `503` |
| `OCR_PROCESSING_FAILED` | 그 외 OCR 처리 중 오류 | `500` |

## Post-MVP 이관

- 문서에 연결된 OCR 작업을 `job_id`로 명시적으로 식별해 사용자가 검수한 작업과 확정 대상 작업을 일치시키는 검증은 [처방 확정 Backend 계약](./prescription-confirmation.md)의 Post-MVP 이관 항목을 따릅니다.

## 검증과 변경 규칙

구현 계약은 `backend/app/tests/ocr`에서 검증합니다.

다음 변경은 이 문서, 구현, API 문서와 관련 테스트를 같은 PR에서 갱신해야 합니다.

- `error_code`·`error_message` 필드 추가·삭제·의미 변경
- 최신 OCR 작업 판별 기준 변경
