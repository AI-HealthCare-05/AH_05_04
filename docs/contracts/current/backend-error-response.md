# ApiError 사용법 및 공통 오류 코드

## 목적

Backend 오류 응답 형식과 오류 코드를 팀 전체가 동일한 기준으로 사용하기 위해 작성한 통합 문서입니다.

## 공통 오류 응답 형식

FastAPI/Starlette 처리 계층까지 도달한 `/api/v1/*` API 오류 응답은 다음 형식을 따릅니다.

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

등록되지 않은 `/api/v1/*` 경로 요청(404)과 지원하지 않는 HTTP 메서드 요청(405)도 전역 `StarletteHTTPException` 핸들러가 공통 형식으로 변환합니다. 이 경우 `code`는 `HTTP_ERROR`, `details`는 빈 배열이며, `message`에는 FastAPI/Starlette의 기본 `detail` 문자열이 들어갑니다. 처리되지 않은 예외(500)도 전역 오류 처리 경계를 거쳐 공통 형식을 따릅니다.

성공과 실패를 포함한 모든 Backend HTTP 응답에는 body의 `trace_id`와 같은 서버 생성 `X-Trace-Id` 응답 Header가 포함됩니다. 성공 응답 body에는 `trace_id`를 추가하지 않습니다. `CORSMiddleware`가 직접 처리하는 CORS preflight 응답은 공통 오류 envelope와 `no-store` 정책의 대상이 아니지만, 가장 바깥 trace 경계가 감싸므로 `X-Trace-Id`는 포함됩니다.

### Local Live validation Header 오류

`X-Validation-Run-Id`는 `local-live-full` 상관관계용이며 인증·인가 수단이 아닙니다. Backend의 `ENV=local`이고 `RELEASE_VALIDATION_ALLOWED=true`인 경우에만 UUID를 수용합니다.

| 조건 | HTTP | code | message |
| --- | ---: | --- | --- |
| 허용된 local 환경에서 UUID 형식 오류 | 400 | `HTTP_ERROR` | `Invalid validation run ID.` |
| Backend 검증 비활성 또는 local 이외 환경에서 Header 존재 | 403 | `HTTP_ERROR` | `Validation run is not allowed.` |

입력 Header 값이나 환경 상세는 오류 message와 details에 포함하지 않습니다.

## 민감정보 노출 방지

**이미 정해진 보안 원칙**: [`SECURITY.md`](../../../SECURITY.md)는 "의료·개인정보 응답은 기본적으로 `Cache-Control: no-store`를 적용합니다"를 현재 원칙으로 정하고 있다. 이는 Post-MVP 목표가 아니라 지금 지켜야 하는 규칙이다. 같은 원칙에 따라 `details[].rejected_value`에도 비밀번호·토큰, OCR·처방 원문, 챗봇 질문·답변, Provider payload, 예외 원문을 넣지 않아야 한다.

**현재 적용 상태**: `backend/app/core/no_store_middleware.py`의 `NoStoreMiddleware`가 `/api/v1/*` 전체 응답에 `Cache-Control: no-store`를 일괄 적용한다. 인증·사용자·처방·의료문서·OCR·가이드·채팅 API의 성공 응답과 오류 응답이 모두 대상이다. 단, Router endpoint와 FastAPI/Starlette 예외 처리 계층까지 도달하지 않고 `CORSMiddleware`가 직접 처리하는 CORS preflight 응답은 공통 오류 envelope와 `no-store` 정책의 대상이 아니다.

**처방 OCR 원문 비노출 원칙**: 처방 확정과 OCR 검수 오류 응답은 OCR `raw_value`, 처방 원문, Provider 원문 오류, 챗봇 질문·답변, 비밀번호·토큰을 `message`나 `details[].rejected_value`에 넣지 않는다. 사용자가 확인한 `confirmed_value`만 처방 확정 입력으로 사용하며, 형식 오류는 `field`와 `reason` 중심으로 반환한다.

## HTTP 상태 코드 기준

| 구분 | HTTP 상태 코드 | 이름 | 사용 기준 |
| --- | --- | --- | --- |
| 성공 | 200 | OK | 조회·수정 요청이 정상 처리되고 응답 본문이 있는 경우 |
| 성공 | 201 | Created | 새로운 리소스가 생성된 경우 |
| 성공 | 202 | Accepted | 비동기 작업 요청이 접수된 경우 (Post-MVP 공통 비동기 기반 도입 이후 사용). 예외: 현재 OCR 실행 API도 `202`를 반환하지만 동기 처리 결과이며 실제 비동기 접수가 아님 — 아래 설명 참고 |
| 성공 | 204 | No Content | 삭제 또는 처리 완료 후 반환할 본문이 없는 경우 (현재 사용자 리소스 삭제 API가 없어 실제로 사용되는 곳은 아직 없음) |
| 클라이언트 오류 | 400 | Bad Request | Pydantic 요청 검증 범위 밖에서 Service가 직접 확인하는 요청 오류 (예: 업로드된 파일이 내용 없는 빈 파일). 요청 자체에 파일 필드가 없는 경우는 FastAPI가 이 코드 실행 전에 자동으로 `422`를 반환한다 |
| 클라이언트 오류 | 401 | Unauthorized | 인증 토큰이 없거나 유효하지 않거나 만료된 경우 |
| 클라이언트 오류 | 403 | Forbidden | 인증은 되었지만 해당 리소스에 접근 권한이 없는 경우 |
| 클라이언트 오류 | 404 | Not Found | 요청한 리소스가 존재하지 않거나 다른 사용자 소유인 경우 |
| 클라이언트 오류 | 409 | Conflict | 현재 리소스 상태와 충돌하여 요청을 처리할 수 없는 경우 |
| 클라이언트 오류 | 422 | Unprocessable Entity | 요청 본문 JSON 문법 오류, 필수 필드 누락 등 Pydantic 요청 검증에 실패한 경우. 현재 Backend는 잘못된 JSON과 필수값 누락을 모두 422로 응답함 |
| 서버 오류 | 500 | Internal Server Error | 서버 내부 오류로 요청 처리에 실패한 경우 |
| 서버 오류 | 503 | Service Unavailable | AI, OCR 등 외부 서비스를 일시적으로 사용할 수 없는 경우 |
| 서버 오류 | 504 | Gateway Timeout | 외부 서비스 응답 시간이 초과된 경우 |

현재 OCR API는 HTTP `202 Accepted`를 반환하지만, MVP에서는 같은 요청 안에서 OCR 처리를 완료한 뒤 결과를 반환합니다. Post-MVP에서 비동기 Job 구조로 전환되면 `202`를 실제 작업 접수 상태를 나타내는 응답으로 사용합니다.

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

### 응답 헤더가 필요한 경우

401 응답처럼 `WWW-Authenticate` 헤더가 함께 나가야 하는 경우 `headers`를 전달합니다.

```python
raise ApiError(
    status_code=401,
    code="UNAUTHORIZED",
    message="로그인이 필요합니다.",
    headers={"WWW-Authenticate": "Bearer"},
)
```

## details 사용법

필드별 오류 정보가 필요한 경우 `ErrorDetail`을 사용합니다.

```python
from app.core.errors import ApiError, ErrorDetail

raise ApiError(
    status_code=422,
    code="PRESCRIPTION_REQUIRED_FIELD_MISSING",
    message="처방 확정에 필요한 항목이 누락되었습니다.",
    details=[ErrorDetail(field="extracted_fields", reason="REQUIRED")],
)
```

## 범위 표시 기준

아래 표는 코드가 실제로 언제 응답에 나가는지에 따라 MVP와 Post-MVP로 나누어 관리합니다. "구현됨/미구현"처럼 시점에 따라 계속 바뀌는 상태 대신, 어느 개발 단계에 속하는 코드인지로 분류해 문서 유지보수 부담을 줄입니다.

- **MVP**: 지금 이 저장소의 코드에서 실제로 발생하는 코드입니다. 코드와 메시지는 실제 구현과 항상 일치해야 합니다.
- **Post-MVP**: 아직 구현되지 않았고, Post-MVP 계획 문서에서 다루는 기능(공통 비동기 Job, 복약 일정·로그, RAG·Citation·Safety 등)이 만들어질 때 사용할 예정인 코드입니다. 지금 이 코드를 실제로 응답에서 받을 일은 없습니다.

## 재시도 가능 여부

`retryable=true`는 일시적인 시간·상태 조건이 해소된 뒤 동일한 payload의 요청이 성공할 수 있음을 뜻합니다.
즉시 또는 자동 재시도를 허용한다는 의미는 아닙니다.
자동·수동 재시도, 지연과 backoff, 최대 횟수, 상태 재조회, 재업로드 및 사용자 안내 방식은 endpoint·domain별 계약에서 결정합니다.
`retryable=false`는 현재 상태에서 동일 요청을 그대로 재전송하는 대상이 아니라는 뜻이며, 재인증·입력 수정·사용자 행동 이후의 새로운 시도까지 금지하지 않습니다.
아래 표가 판정의 정본이며 다른 절의 표는 이 기준을 따릅니다.
`retryable`은 아직 응답 body에 포함하지 않고 클라이언트는 `code`로 판정합니다.
응답 필드나 `Retry-After` header 추가는 별도 Decision이 필요합니다.

### 공개 오류 코드

| code | HTTP | `retryable` | 판정 근거 |
| --- | ---: | :---: | --- |
| `OCR_PROVIDER_TIMEOUT` | 503 | true | OCR 제공자 일시 지연 |
| `OCR_PROVIDER_CALL_FAILED` | 503 | true | OCR 제공자 연결 일시 실패 |
| `OCR_PROVIDER_UNAVAILABLE` | 503 | true | OCR 제공자 일시 사용 불가 |
| `SERVICE_UNAVAILABLE` | 503 | true | 서비스 일시 사용 불가 |
| `GATEWAY_TIMEOUT` | 504 | true | 외부 처리 시간 초과 |
| `CONCURRENT_UPDATE_IN_PROGRESS` | 409 | true | 문서 잠금 경합이며 잠금 해제 후 성공 가능 |
| `OCR_JOB_ALREADY_PROCESSING` | 409 | true | 진행 중 작업이 끝나면 성공 가능 |
| `OCR_JOB_NOT_COMPLETED` | 409 | true | OCR이 완료되면 성공 가능 |
| `PRESCRIPTION_ALREADY_CONFIRMED` | 409 | false | 이미 확정된 terminal 상태 |
| `CONFLICT` | 409 | false | 중복 리소스 등 요청 내용을 바꿔야 하는 충돌 |
| `VALIDATION_FAILED` | 422 | false | 요청 값 수정 필요 |
| `PRESCRIPTION_REQUIRED_FIELD_MISSING` | 422 | false | 누락 항목 입력 필요 |
| `BAD_REQUEST` | 400 | false | 요청 내용 수정 필요 |
| `UPLOAD_FILE_TOO_LARGE` | 400 | false | 다른 파일 필요 |
| `UPLOAD_FILE_INVALID_TYPE` | 400 | false | 다른 파일 필요 |
| `UNAUTHORIZED` | 401 | false | 재인증 필요이며 동일 요청 재전송 대상이 아님 |
| `INVALID_TOKEN` | 401 | false | 재인증 필요 |
| `EXPIRED_TOKEN` | 401 | false | 토큰 갱신 필요 |
| `FORBIDDEN` | 403 | false | 권한 상태 변경 필요 |
| `MEDICAL_DOCUMENT_NOT_FOUND` | 404 | false | 리소스 부재 |
| `OCR_JOB_NOT_FOUND` | 404 | false | 리소스 부재 |
| `EXTRACTED_FIELD_NOT_FOUND` | 404 | false | 리소스 부재 |
| `PRESCRIPTION_NOT_FOUND` | 404 | false | 리소스 부재 |
| `GUIDE_NOT_FOUND` | 404 | false | 리소스 부재 |
| `CHAT_SESSION_NOT_FOUND` | 404 | false | 리소스 부재 |
| `OCR_PROCESSING_FAILED` | 500 | false | 원인 확인 없이 재전송하면 같은 실패가 반복됨 |
| `GUIDE_GENERATION_FAILED` | 500 | false | 동일 |
| `AI_RESPONSE_FAILED` | 500 | false | 동일 |
| `INTERNAL_SERVER_ERROR` | 500 | false | 동일 |
| `HTTP_ERROR` | (원본) | false | 자동 변환 결과이므로 판정하지 않음 |

`503`·`504`는 모두 `retryable=true`이고, `401`은 재인증이 필요하므로 `false`입니다.
같은 `409`라도 잠금·진행 중처럼 시간이 지나면 해소되는 충돌만 `true`입니다.

### Worker `FailureCode` 매핑

Worker는 `ai_worker/core/retry.py`의 `RETRYABLE_FAILURE_CODES`로 재시도를 판정합니다.
공개 오류 코드와의 대응은 다음과 같습니다.

| `FailureCode` | `retryable` | 대응 공개 오류 코드 |
| --- | :---: | --- |
| `TIMEOUT` | true | `OCR_PROVIDER_TIMEOUT` |
| `DEPENDENCY_UNAVAILABLE` | true | `OCR_PROVIDER_CALL_FAILED`, `OCR_PROVIDER_UNAVAILABLE` |
| `INVALID_INPUT` | false | `VALIDATION_FAILED` |
| `UNSUPPORTED_SCHEMA` | false | `OCR_PROCESSING_FAILED` |
| `SAFETY_VALIDATION_FAILED` | false | 공개 오류가 아니라 Safety 상태 축 |
| `RETRY_EXHAUSTED` | false | `OCR_PROCESSING_FAILED` |
| `INTERNAL_ERROR` | false | `INTERNAL_SERVER_ERROR` |

`SAFETY_VALIDATION_FAILED`는 오류 응답이 아니라 정상 응답의 상태 축으로 표현합니다.
확정 정의는 [Safety Result 계약 v1](../targets/post-mvp-1/safety-result-v1.md)을 따릅니다.

Worker 재시도 지연은 `min(5초 × 2^(attempt_count-1), 60초)`에 0~20% 양의 jitter를 더합니다.
`retryable=false`인 오류는 지연을 계산하지 않고 즉시 후속 처리로 넘깁니다.

### 변경 규칙

`RETRYABLE_FAILURE_CODES`와 이 절의 표는 계약 테스트로 고정합니다.
한쪽만 바꾸면 `tests/contract/test_retryable_error_classification.py`가 실패합니다.

## 공통 오류 코드

### MVP

| HTTP | code | message | 비고 |
| --- | --- | --- | --- |
| 400 | `BAD_REQUEST` | 상황에 따른 안내 문구 (예: "업로드할 파일을 선택해 주세요.") | |
| 401 | `UNAUTHORIZED` | "로그인이 필요합니다." 또는 "이메일 또는 비밀번호가 올바르지 않습니다." | 인증 토큰 누락, 로그인 실패 |
| 401 | `INVALID_TOKEN` | "인증 정보가 유효하지 않습니다. 다시 로그인해 주세요." | |
| 401 | `EXPIRED_TOKEN` | "인증 정보가 만료되었습니다. 다시 로그인해 주세요." | |
| 403 | `FORBIDDEN` | "비활성화된 계정입니다." | 현재는 비활성 계정 로그인 시도에만 사용 |
| 409 | `CONFLICT` | 상황에 따른 안내 문구 (예: "이미 사용중인 이메일입니다.") | 회원가입 중복, 종료된 대화 세션 등 여러 상황에서 재사용 |
| 422 | `VALIDATION_FAILED` | 상황에 따른 안내 문구 (예: "입력값을 확인해 주세요.", "MVP에서는 처방전 문서만 업로드할 수 있습니다.") | Pydantic 요청 검증 실패 시 자동 발생 또는 Service에서 수동 발생 |
| 500 | `INTERNAL_SERVER_ERROR` | "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요." | 예상하지 못한 예외의 최종 fallback |
| 503 | `SERVICE_UNAVAILABLE` | "현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요." | |
| 504 | `GATEWAY_TIMEOUT` | "외부 처리 시간이 초과되었습니다. 다시 시도해 주세요." | |
| (원본 상태 코드) | `HTTP_ERROR` | `HTTPException.detail`을 문자열로 변환한 값 (`str(detail)`) | 아직 `ApiError`로 전환되지 않은 코드의 자동 변환 결과. 기본 라우팅 404/405도 이 형식으로 변환됨 |

각 코드의 재시도 가능 여부는 「재시도 가능 여부」 절의 표를 따릅니다.

### Post-MVP

이 문서는 현재 Backend 오류 응답 envelope와 MVP에서 실제 응답으로 나가는 오류 코드의 기준 문서다. Post-MVP target 전용 오류 코드는 [비동기 Job 계약 v1](../targets/post-mvp-1/async-job-v1.md), [멱등성 계약 v1](../targets/post-mvp-1/idempotency-v1.md), [처방 버전 계약 v1](../targets/post-mvp-1/prescription-version-v1.md)처럼 승인된 목표 계약에서 먼저 정의하고, 실제 구현 PR에서 이 문서와 코드·테스트를 함께 갱신한다. 승인된 Decision이나 목표 계약이 없는 코드(`CONSENT_REQUIRED`, `RESOURCE_NOT_FOUND`, `RATE_LIMITED` 등)는 어떤 문서에도 등록하지 않는다. 오류 코드·HTTP status 추가는 새 Decision 또는 Contract Freeze 갱신이 필요하다([AGENTS.md](../../../AGENTS.md) 기준).

## 도메인별 오류 코드

### MVP

| HTTP | code | message | 사용 상황 |
| --- | --- | --- | --- |
| 404 | `PRESCRIPTION_NOT_FOUND` | "처방 정보를 찾을 수 없습니다." | 요청한 처방 ID가 존재하지 않거나 다른 사용자 소유 |
| 409 | `PRESCRIPTION_ALREADY_CONFIRMED` | "이미 확정된 처방 정보입니다." | 이미 확정된 처방을 다시 확정하거나, 확정된 문서의 extracted-field를 수정하려고 함 |
| 409 | `CONCURRENT_UPDATE_IN_PROGRESS` | "같은 문서에 대한 다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요." | 같은 문서의 처방 확정과 extracted-field PATCH가 동시에 요청되어 문서 row 잠금을 3초 안에 획득하지 못함 |
| 422 | `PRESCRIPTION_REQUIRED_FIELD_MISSING` | "처방 확정에 필요한 항목이 누락되었습니다." | 처방 확정 요청에 필수 항목이 없음 |
| 409 | `OCR_JOB_NOT_COMPLETED` | "OCR 처리가 완료된 결과가 없어 처방을 확정할 수 없습니다." | OCR이 완료되기 전에 처방 확정을 요청함 |
| 400 | `UPLOAD_FILE_TOO_LARGE` | "파일 크기는 10MB 이하만 업로드할 수 있습니다." | 10MB를 초과한 파일을 업로드함 |
| 400 | `UPLOAD_FILE_INVALID_TYPE` | 상황별 안내 문구 (형식 미지원 / 확장자·MIME 불일치 / 시그니처 불일치) | 허용되지 않은 파일 형식을 업로드함 |
| 404 | `MEDICAL_DOCUMENT_NOT_FOUND` | "의료문서를 찾을 수 없습니다." | 요청한 의료 문서가 없거나 다른 사용자 소유 |
| 404 | `OCR_JOB_NOT_FOUND` | "OCR 작업 정보를 찾을 수 없습니다." | 요청한 OCR 작업 ID가 존재하지 않거나 다른 사용자 소유 |
| 409 | `OCR_JOB_ALREADY_PROCESSING` | "이미 OCR 처리가 진행 중입니다." | 같은 문서에 대해 OCR 작업이 이미 진행 중임 |
| 503 | `OCR_PROVIDER_TIMEOUT` | "OCR 서비스 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요." | OCR 제공 서비스 호출이 시간 초과됨 |
| 503 | `OCR_PROVIDER_CALL_FAILED` | "OCR 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요." | OCR 제공 서비스 연결 자체가 실패함 |
| 503 | `OCR_PROVIDER_UNAVAILABLE` | "OCR 서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요." | OCR 제공 서비스를 일시적으로 사용할 수 없음 |
| 500 | `OCR_PROCESSING_FAILED` | "처방전 인식에 실패했습니다. 다시 시도하거나 직접 입력해 주세요." | OCR 처리 자체가 실패함 |
| 404 | `EXTRACTED_FIELD_NOT_FOUND` | "추출 필드를 찾을 수 없습니다." | 요청한 OCR 추출 필드 ID가 존재하지 않거나 다른 사용자 소유 |
| 404 | `GUIDE_NOT_FOUND` | "가이드를 찾을 수 없습니다." | 요청한 복약 가이드가 존재하지 않거나 다른 사용자 소유 |
| 500 | `GUIDE_GENERATION_FAILED` | "복약 가이드 생성에 실패했습니다. 다시 시도해 주세요." | AI 가이드 생성 처리에 실패함 |
| 404 | `CHAT_SESSION_NOT_FOUND` | "대화 세션을 찾을 수 없습니다." | 요청한 상담 세션이 존재하지 않거나 다른 사용자 소유 |
| 500 | `AI_RESPONSE_FAILED` | "AI 답변 생성에 실패했습니다." | AI 답변 생성에 실패함 |

가이드·챗봇 생성은 현재 한 요청 안에서 동기적으로 완료된 뒤 `201 Created`로 응답합니다. 별도의 "생성 중" 상태나 진행률 조회는 없습니다.

### Post-MVP

Post-MVP 도메인별 오류 코드는 각 승인된 목표 계약에서 먼저 정의한다 — 예: `PRESCRIPTION_MEDICATION_REQUIRED`(422)는 [처방 버전 계약 v1](../targets/post-mvp-1/prescription-version-v1.md). 실제 구현 PR에서는 이 문서의 MVP 표 또는 후속 Post-MVP 구현 표에 반영해 코드와 문서가 같은 기준을 보도록 한다.

아직 어떤 목표 계약에도 없어 별도 Decision이 필요한 항목:

- `PROFILE_NOT_FOUND`, `UPLOAD_FAILED`, `MEDICATION_SCHEDULE_NOT_FOUND`, `MEDICATION_LOG_ALREADY_EXISTS`, `MEDICATION_LOG_INVALID_STATUS`: 뒷받침하는 승인된 계약이 없어 어떤 문서에도 등록하지 않는다.
- `OCR_LOW_CONFIDENCE`: 저신뢰 OCR 결과를 요청 실패(`422`)로 볼지, OCR 결과 검수 상태(`REVIEW_REQUIRED`, 아직 미구현)로 볼지는 Product·OCR·Frontend·Backend가 함께 결정해야 하는 별도 Decision 사안이다. 이번 PR에서는 HTTP status를 확정하지 않는다.
- `CITATION_NOT_FOUND`는 아직 승인되지 않은 출처 상세 조회 API를 전제하며, 기존 [Safety Result 계약 v1](../targets/post-mvp-1/safety-result-v1.md)의 `fallback_code`(`NO_APPROVED_EVIDENCE` 등)와 의미가 겹칠 수 있어 해당 API 승인 시 함께 재검토한다.

가이드 생성 작업이 정상적으로 접수되었다는 뜻의 `202`는 오류가 아니라 성공 응답이므로 이 표에 두지 않습니다. Post-MVP-1에서 공통 비동기 Job 기반이 도입되면 `202 {"data": JobStatusResponse}` 형태로 접수되며, 세부 응답 형태는 [비동기 Job 계약 v1](../targets/post-mvp-1/async-job-v1.md)을 따릅니다.

AI가 안전 제한이나 근거 부족으로 답변을 제한하는 경우는 오류 코드가 아니라 정상 응답의 상태 축으로 구분합니다. 확정된 상태 축은 [Safety Result 계약 v1](../targets/post-mvp-1/safety-result-v1.md)을 따르며, 요약은 [복약 챗봇 Backend-AI Core 계약](./medication-chat-ai-backend.md)에 둡니다.

## 오류 코드 구분 기준

- OCR 작업 자체가 실패하면 `OCR_PROCESSING_FAILED`를 사용합니다.
- OCR 작업은 완료됐지만 특정 결과 항목이 없으면 `EXTRACTED_FIELD_NOT_FOUND`를 사용합니다.
- OCR 제공 서비스 호출이 시간 초과되면 `OCR_PROVIDER_TIMEOUT`, 연결 자체가 실패하면 `OCR_PROVIDER_CALL_FAILED`, 그 외 일시적으로 사용할 수 없으면 `OCR_PROVIDER_UNAVAILABLE`을 사용합니다.
- 일반적인 리소스 상태 충돌은 `CONFLICT`를 사용하고, 동일 OCR 작업 중복처럼 의미가 명확한 경우에는 `OCR_JOB_ALREADY_PROCESSING`을 사용합니다.

## HTTPException과의 구분

도메인 오류와 공통 오류 코드가 정의된 상황에서는 `HTTPException` 대신 `ApiError`를 사용합니다.

```python
raise ApiError(
    status_code=404,
    code="GUIDE_NOT_FOUND",
    message="가이드를 찾을 수 없습니다.",
)
```

전역 `HTTPException` 핸들러도 응답을 `{code, message, details, trace_id}` 형식으로 변환하지만, `code`는 `HTTP_ERROR`로 고정됩니다. 세부적인 공통·도메인 코드가 필요한 경우에는 `ApiError`를 사용합니다.

## 새 오류 코드 추가 기준

- 기존 코드와 의미가 중복되지 않는지 먼저 확인합니다.
- 새 오류 코드나 기존 코드의 의미 변경은 [AGENTS.md](../../../AGENTS.md) 기준에 따라 먼저 팀 Decision 또는 Contract Freeze 승인을 받습니다. 승인 전에는 MVP·Post-MVP 표 어디에도 등록하지 않습니다.
- 승인된 뒤에 HTTP 상태 코드와 사용자 메시지를 함께 정의하고, 실제 사용 상황 예시를 문서에 추가합니다.
- 이 문서와 Backend 코드, 관련 테스트를 같은 PR에서 함께 갱신합니다. 실제 구현이 아직 없는 경우에만 "Post-MVP" 표에 등록하되, 이 역시 사전 승인이 있어야 합니다.
