# ApiError 사용법 및 공통 오류 코드

## 목적

Backend 오류 응답 형식과 에러 코드를 팀 전체가 동일한 기준으로 사용하기 위해 작성한 통합 문서입니다. 

## 공통 오류 응답 형식

모든 오류 응답은 다음 형식을 따릅니다.
```json
{
 "code": "PRESCRIPTION_NOT_FOUND",
 "message": "처방 정보를 찾을 수 없습니다.",
 "details": [],
 "trace_id": "요청별 추적 ID"
}
```

| 필드 | 설명 |
| --- | --- |
| `code` | 클라이언트가 오류 유형을 식별하는 공통 코드 |
| `message` | 오류 상황을 설명하는 메시지 |
| `details` | 필드별 오류 등 추가 정보 목록. 없으면 빈 배열 사용 |
| `trace_id` | 요청 추적을 위한 ID | 

클라이언트는 `message`가 아니라 `code`를 기준으로 오류를 분기합니다.

 ## HTTP 상태 코드 기준

| 구분 | HTTP 상태 코드 | 이름 | 사용 기준 |
| --- | --- | --- | --- |
| 성공 | 200 | OK | 조회·수정 요청이 정상 처리되고 응답 본문이 있는 경우 |
| 성공 | 201 | Created | 새로운 리소스가 생성된 경우 |
| 성공 | 202 | Accepted | OCR, AI 가이드 생성 등 비동기 작업 요청이 접수된 경우 |
| 성공 | 204 | No Content | 삭제 또는 처리 완료 후 반환할 본문이 없는 경우 |
| 클라이언트 오류 | 400 | Bad Request | 요청 형식, JSON 문법, 필수 헤더 등이 잘못된 경우 |
| 클라이언트 오류 | 401 | Unauthorized | 인증 토큰이 없거나 유효하지 않거나 만료된 경우 |
| 클라이언트 오류 | 403 | Forbidden | 인증은 되었지만 해당 리소스에 접근 권한이 없는 경우 |
| 클라이언트 오류 | 404 | Not Found | 요청한 리소스가 존재하지 않는 경우 |
| 클라이언트 오류 | 409 | Conflict | 현재 리소스 상태와 충돌하여 요청을 처리할 수 없는 경우 |
| 클라이언트 오류 | 412 | Precondition Failed | `If-Match`, `version`, `ETag` 조건이 맞지 않는 경우 |
| 클라이언트 오류 | 422 | Unprocessable Entity | 요청 형식은 맞지만 필드 값 검증에 실패한 경우 |
| 클라이언트 오류 | 429 | Too Many Requests | 호출 횟수 제한을 초과한 경우 |
| 서버 오류 | 500 | Internal Server Error | 서버 내부 오류로 요청 처리에 실패한 경우 |
| 서버 오류 | 503 | Service Unavailable | AI, OCR, RAG 등 외부 서비스 또는 서버를 일시적으로 사용할 수 없는 경우 |
| 서버 오류 | 504 | Gateway Timeout | 외부 서비스 응답 시간이 초과된 경우 |


## ApiError 기본 사용법

`app.core.errors.ApiError`를 가져온 뒤 오류 상황에서 `raise`합니다. 

```python
from app.core.errors import ApiError

raise ApiError(
 status_code=404,
 code="PRESCRIPTION_NOT_FOUND",
 message="처방 정보를 찾을 수 없습니다.",
)
```

`ApiError`가 발생하면 전역 예외 핸들러가 공통 응답 형식으로 변환하고 요청의 `trace_id`를 자동으로 포함합니다. 

## details 사용법

필드별 오류 정보가 필요한 경우 `ErrorDetail`을 사용합니다. 

```python
from app.core.errors import ApiError, ErrorDetail

raise ApiError(
 status_code=422,
 code="PRESCRIPTION_REQUIRED_FIELD_MISSING",
 message="처방 확정에 필요한 항목이 누락되었습니다.",
 details=[
 ErrorDetail(
 field="medications",
 reason="REQUIRED",
 rejected_value=None,
 )
 ],
)
```

## 공통 오류 코드

| HTTP | code | message |
| --- | --- | --- | --- |
| 400 | `BAD_REQUEST` | 요청 형식이 올바르지 않습니다. |
| 401 | `UNAUTHORIZED` | 로그인이 필요합니다. 
| 401 | `INVALID_TOKEN` | 인증 정보가 유효하지 않습니다. 다시 로그인해 주세요. |
| 401 | `EXPIRED_TOKEN` | 인증 정보가 만료되었습니다. 다시 로그인해 주세요. |
| 403 | `FORBIDDEN` | 해당 리소스에 접근할 권한이 없습니다. |
| 403 | `CONSENT_REQUIRED` | 서비스 이용을 위해 필수 동의가 필요합니다. |
| 404 | `RESOURCE_NOT_FOUND` | 요청한 정보를 찾을 수 없습니다. |
| 409 | `CONFLICT` | 현재 상태에서는 요청을 처리할 수 없습니다. |
| 409 | `IDEMPOTENCY_CONFLICT` | 동일한 중복 요청 키로 다른 요청이 전달되었습니다. |
| 412 | `VERSION_CONFLICT` | 다른 곳에서 먼저 수정된 정보입니다. 새로고침 후 다시 시도해 주세요. |
| 422 | `VALIDATION_FAILED` | 입력값을 확인해 주세요. |
| 429 | `RATE_LIMITED` | 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요. |
| 500 | `INTERNAL_SERVER_ERROR` | 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. |
| 503 | `SERVICE_UNAVAILABLE` | 현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요. |
| 504 | `GATEWAY_TIMEOUT` | 외부 처리 시간이 초과되었습니다. 다시 시도해 주세요. |

## 도메인별 오류 코드 및 AI 응답 상태

| HTTP | code | message | 사용 상황 예시 | 구현 상태 |
| --- | --- | --- | --- | --- |
| 404 | `PROFILE_NOT_FOUND` | 사용자 프로필을 찾을 수 없습니다. | 요청한 사용자 프로필이 존재하지 않음 |
| 404 | `PRESCRIPTION_NOT_FOUND` | 처방 정보를 찾을 수 없습니다. | 요청한 처방 ID가 존재하지 않음 |
| 409 | `PRESCRIPTION_ALREADY_CONFIRMED` | 이미 확정된 처방 정보입니다. | 이미 확정된 처방을 다시 확정하려고 함 |
| 422 | `PRESCRIPTION_REQUIRED_FIELD_MISSING` | 처방 확정에 필요한 항목이 누락되었습니다. | 처방 확정 요청에 필수 항목이 없음 |
| 400 | `UPLOAD_FILE_INVALID_TYPE` | 지원하지 않는 파일 형식입니다. JPG, PNG, PDF 파일만 업로드할 수 있습니다. | 허용되지 않은 확장자 또는 파일 형식을 업로드함 |
| 400 | `UPLOAD_FILE_TOO_LARGE` | 파일 크기는 10MB 이하만 업로드할 수 있습니다. | 10MB를 초과한 파일을 업로드함 |
| 500 | `UPLOAD_FAILED` | 파일 업로드에 실패했습니다. 다시 시도해 주세요. | 파일 저장 또는 업로드 처리 중 오류가 발생함 |
| 404 | `MEDICAL_DOCUMENT_NOT_FOUND` | 의료 문서를 찾을 수 없습니다. | 요청한 의료 문서가 없거나 삭제됨 |
| 404 | `OCR_JOB_NOT_FOUND` | OCR 작업 정보를 찾을 수 없습니다. | 요청한 OCR 작업 ID가 존재하지 않음 |
| 409 | `OCR_JOB_ALREADY_PROCESSING` | 해당 문서의 OCR 작업이 이미 처리 중입니다. | 같은 문서에 대해 OCR 작업이 이미 `PENDING` 또는 `PROCESSING` 상태임 |
| 409 | `OCR_JOB_NOT_COMPLETED` | OCR 처리가 아직 완료되지 않았습니다. | OCR 작업이 완료되기 전에 결과를 요청함 |
| 503 | `OCR_PROVIDER_TIMEOUT` | OCR 서비스 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요. | OCR 제공 서비스 호출이 시간 초과됨 |
| 503 | `OCR_PROVIDER_CALL_FAILED` | OCR 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요. | OCR 제공 서비스 연결 자체가 실패함 |
| 503 | `OCR_PROVIDER_UNAVAILABLE` | OCR 서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요. | OCR 제공 서비스를 일시적으로 사용할 수 없음 |
| 500 | `OCR_PROCESSING_FAILED` | 처방전 인식에 실패했습니다. 다시 시도하거나 직접 입력해 주세요. | OCR 처리 자체가 실패함 |
| 404 | `EXTRACTED_FIELD_NOT_FOUND` | OCR 결과에서 요청한 항목을 찾을 수 없습니다. | OCR 결과에 `dosage` 또는 `frequency` 항목이 없음 |
| 422 | `OCR_LOW_CONFIDENCE` | 일부 항목의 인식 신뢰도가 낮습니다. 내용을 확인해 주세요. | OCR 결과의 인식 신뢰도가 기준보다 낮음 | 
| 404 | `MEDICATION_SCHEDULE_NOT_FOUND` | 복약 일정을 찾을 수 없습니다. | 요청한 복약 일정이 존재하지 않음 |
| 409 | `MEDICATION_LOG_ALREADY_EXISTS` | 해당 시간의 복약 기록이 이미 저장되었습니다. | 같은 시간의 복약 기록이 이미 존재함 |
| 422 | `MEDICATION_LOG_INVALID_STATUS` | 복약 기록 상태값이 올바르지 않습니다. | 허용되지 않은 복약 기록 상태값을 전달함 |
| 404 | `GUIDE_NOT_FOUND` | 생성된 복약 가이드를 찾을 수 없습니다. | 요청한 복약 가이드가 존재하지 않음 |
| 202 | `GUIDE_GENERATION_PENDING` | 복약 가이드 생성이 진행 중입니다. | 가이드 생성 작업이 접수되었지만 아직 완료되지 않음 | 
| 500 | `GUIDE_GENERATION_FAILED` | 복약 가이드 생성에 실패했습니다. 다시 시도해 주세요. | AI 가이드 생성 처리에 실패함 | 
| 404 | `CHAT_SESSION_NOT_FOUND` | 상담 세션을 찾을 수 없습니다. | 요청한 상담 세션이 존재하지 않음 |
| 500 | `AI_RESPONSE_FAILED` | 답변 생성에 실패했습니다. 다시 시도해 주세요. | AI 답변 생성에 실패함 |
| 500 | `INTERNAL_SERVER_ERROR` | 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. | 서버 데이터 무결성 문제 등 예상하지 못한 내부 오류가 발생함 |
| 200 | `ANSWERED` | 답변을 정상적으로 생성했습니다. | AI가 근거를 바탕으로 답변을 정상 생성함 |
| 200 | `SAFETY_BLOCKED` | 안전상의 이유로 답변을 제공할 수 없습니다. 의료진과 상담해 주세요. | 안전 정책에 따라 답변을 제한함 | 
| 200 | `EVIDENCE_UNAVAILABLE` | 신뢰할 수 있는 근거가 부족해 답변을 제한합니다. | 근거 부족으로 답변을 제한함. 기존 `EVIDENCE_NOT_FOUND`를 대체함 |
| 404 | `CITATION_NOT_FOUND` | 답변의 출처 정보를 찾을 수 없습니다. | 별도의 출처 상세 조회 API에서 요청한 출처 ID가 없음 |

AI가 요청을 처리한 결과로 안전 제한이나 근거 부족이 발생한 경우에는 오류 응답이 아닌 정상 응답으로 처리합니다. 정상적으로 처리된 AI 결과는 `status`로 구분하고, 실제 요청·서버·처리 오류는 `ApiError`의 `code`로 구분합니다. 

생성된 답변에 필요한 출처가 누락된 경우에는 `500 AI_RESPONSE_FAILED`, 서버 데이터 무결성 문제로 출처를 조회하지 못한 경우에는 `500 INTERNAL_SERVER_ERROR`를 사용합니다. `CITATION_NOT_FOUND`는 출처 상세 조회 API의 리소스 없음에만 사용합니다. 

## 오류 코드 구분 기준

- OCR 작업 자체가 실패하면 `OCR_PROCESSING_FAILED`를 사용합니다.
- OCR 작업은 완료됐지만 특정 결과 항목이 없으면 `EXTRACTED_FIELD_NOT_FOUND`를 사용합니다.
- OCR 결과의 인식 신뢰도가 낮으면 `OCR_LOW_CONFIDENCE`를 사용합니다.
- OCR 제공 서비스 호출이 시간 초과되면 `OCR_PROVIDER_TIMEOUT`, 연결 자체가 실패하면 `OCR_PROVIDER_CALL_FAILED`, 그 외 일시적으로 사용할 수 없으면 `OCR_PROVIDER_UNAVAILABLE`을 사용합니다.
- 일반적인 리소스 상태 충돌은 `CONFLICT`를 사용하고, 동일 OCR 작업 중복처럼 의미가 명확한 경우에는 `OCR_JOB_ALREADY_PROCESSING`을 사용합니다.

`EXTRACTED_FIELD_NOT_FOUND`는 현재 Backend 로직의 의미에 따라 OCR 결과 자체에 요청한 필드가 없는 경우 `404`로 사용합니다. OCR 결과는 존재하지만 필수 데이터로 사용할 수 없는 경우에는 `422`로 구분할 수 있습니다.

## HTTPException과의 구분

도메인 오류와 공통 오류 코드가 정의된 상황에서는 `HTTPException` 대신 `ApiError`를 사용합니다. 

```python
raise ApiError(
 status_code=404,
 code="GUIDE_NOT_FOUND",
 message="생성된 복약 가이드를 찾을 수 없습니다.",
)
```

현재 전역 `HTTPException` 핸들러도 응답을 `{code, message, details, trace_id}` 형식으로 변환하지만, code는 `HTTP_ERROR`로 처리됩니다. 따라서 세부적인 공통·도메인 코드가 필요한 경우에는 `ApiError`를 사용합니다. 

## 새 오류 코드 추가 기준

- 기존 코드와 의미가 중복되지 않는지 먼저 확인합니다. - HTTP 상태 코드와 사용자 메시지를 함께 정의합니다. - 실제 사용 상황 예시를 문서에 추가합니다. - 팀 공통 문서에 등록한 뒤 Backend 코드에 적용합니다. 
