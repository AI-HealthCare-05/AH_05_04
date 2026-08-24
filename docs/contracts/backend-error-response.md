# ApiError 사용법 및 공통 오류 코드

## 목적

Backend 오류 응답 형식과 오류 코드를 팀 전체가 동일한 기준으로 사용하기 위해 작성한 통합 문서입니다.

## 공통 오류 응답 형식

등록된 API에서 처리하는 오류 응답은 다음 형식을 따릅니다.

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

등록되지 않은 경로 요청(404)이나 지원하지 않는 HTTP 메서드 요청(405)은 FastAPI/Starlette가 라우팅 단계에서 자체적으로 응답을 만들기 때문에 아직 이 공통 형식을 따르지 않습니다. 현재는 등록되지 않은 경로에 `{"detail": "Not Found"}`, 지원하지 않는 메서드에 `{"detail": "Method Not Allowed"}`가 반환됩니다. 클라이언트는 이 두 경우에 `code` 필드가 없을 수 있다는 점을 감안해야 합니다.

## 민감정보 노출 방지

**목표 계약**: 의료·개인정보 또는 인증정보가 포함될 수 있는 API는 성공 응답뿐 아니라 `4xx`·`5xx` 오류 응답에도 `Cache-Control: no-store`를 적용한다. `details[].rejected_value`에는 비밀번호·토큰, OCR·처방 원문, 챗봇 질문·답변, Provider payload, 예외 원문을 넣지 않는다.

**현재 구현 상태**: 부분 적용. 처방·의료문서·OCR·가이드 API는 각 라우터의 성공 응답에만 개별적으로 `Cache-Control: no-store`를 붙이며, 공통 오류 핸들러(`app/core/errors.py`)는 기본으로 이 헤더를 붙이지 않는다. Chat API만 `ChatNoStoreMiddleware`로 성공·오류 응답 모두를 보호한다. `details[].rejected_value`도 일부 검증 코드(예: 처방 확정의 `dose_value` 형식 오류)에서 원본 입력값을 그대로 담고 있어 위 목표를 아직 충족하지 못한다. 이 문구는 현재 구현 완료를 의미하지 않으며, 실제 적용 범위 확장과 회귀 테스트는 별도 후속 Issue에서 진행한다.

## HTTP 상태 코드 기준

| 구분 | HTTP 상태 코드 | 이름 | 사용 기준 |
| --- | --- | --- | --- |
| 성공 | 200 | OK | 조회·수정 요청이 정상 처리되고 응답 본문이 있는 경우 |
| 성공 | 201 | Created | 새로운 리소스가 생성된 경우 |
| 성공 | 202 | Accepted | 비동기 작업 요청이 접수된 경우 (Post-MVP 공통 비동기 기반 도입 이후 사용) |
| 성공 | 204 | No Content | 삭제 또는 처리 완료 후 반환할 본문이 없는 경우 (현재 사용자 리소스 삭제 API가 없어 실제로 사용되는 곳은 아직 없음) |
| 클라이언트 오류 | 400 | Bad Request | Pydantic 요청 검증 범위 밖에서 Service가 직접 확인하는 요청 오류 (예: 업로드 파일 누락) |
| 클라이언트 오류 | 401 | Unauthorized | 인증 토큰이 없거나 유효하지 않거나 만료된 경우 |
| 클라이언트 오류 | 403 | Forbidden | 인증은 되었지만 해당 리소스에 접근 권한이 없는 경우 |
| 클라이언트 오류 | 404 | Not Found | 요청한 리소스가 존재하지 않거나 다른 사용자 소유인 경우 |
| 클라이언트 오류 | 409 | Conflict | 현재 리소스 상태와 충돌하여 요청을 처리할 수 없는 경우 |
| 클라이언트 오류 | 412 | Precondition Failed | 별도로 승인될 `If-Match`/`ETag` 조건부 요청 실패 (Post-MVP, 아직 확정된 계약 없음). 처방 버전 충돌은 이 행이 아니라 `409 PRESCRIPTION_VERSION_CONFLICT`를 사용한다 |
| 클라이언트 오류 | 422 | Unprocessable Entity | 요청 본문 JSON 문법 오류, 필수 필드 누락 등 Pydantic 요청 검증에 실패한 경우. 현재 Backend는 잘못된 JSON과 필수값 누락을 모두 422로 응답함 |
| 클라이언트 오류 | 429 | Too Many Requests | 호출 횟수 제한을 초과한 경우 (Post-MVP) |
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
| (원본 상태 코드) | `HTTP_ERROR` | `HTTPException.detail`을 문자열로 변환한 값 (`str(detail)`) | 아직 `ApiError`로 전환되지 않은 코드의 자동 변환 결과. `detail`이 문자열이 아니면 그 값의 문자열 표현이 됨 |

### Post-MVP

| HTTP | code | message | 비고 |
| --- | --- | --- | --- |
| 409 | `IDEMPOTENCY_KEY_CONFLICT` | 이전과 다른 내용의 요청이라 처리할 수 없습니다. 새로 요청해 주세요. | Idempotency-Key 도입 후 사용. 명칭·상태는 [멱등성 계약 v1](./idempotency-v1.md) 확정값 기준 |
| 409 | `PRESCRIPTION_VERSION_CONFLICT` | 다른 곳에서 먼저 수정된 정보입니다. 새로고침 후 다시 시도해 주세요. | Prescription Version 도입 후 사용. 명칭·상태는 [처방 버전 계약 v1](./prescription-version-v1.md) 확정값 기준 |

`CONSENT_REQUIRED`, `RESOURCE_NOT_FOUND`, `RATE_LIMITED`는 뒷받침하는 승인된 Decision이나 목표 계약이 아직 없어 이 표에서 제외했다. 오류 코드·HTTP status 추가는 새 Decision 또는 Contract Freeze 갱신이 필요하다([AGENTS.md](../../AGENTS.md) 기준).

## 도메인별 오류 코드

### MVP

| HTTP | code | message | 사용 상황 |
| --- | --- | --- | --- |
| 404 | `PRESCRIPTION_NOT_FOUND` | "처방 정보를 찾을 수 없습니다." | 요청한 처방 ID가 존재하지 않거나 다른 사용자 소유 |
| 409 | `PRESCRIPTION_ALREADY_CONFIRMED` | "이미 확정된 처방 정보입니다." | 이미 확정된 처방을 다시 확정하려고 함 |
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

도메인별 Post-MVP 오류 코드는 아직 이 저장소에 승인된 Decision이나 목표 계약이 없어 표를 비워둔다. 다음 코드 후보는 뒷받침하는 계약이 확정된 뒤에만 이 표에 추가한다.

- `PROFILE_NOT_FOUND`, `UPLOAD_FAILED`, `MEDICATION_SCHEDULE_NOT_FOUND`, `MEDICATION_LOG_ALREADY_EXISTS`, `MEDICATION_LOG_INVALID_STATUS`, `CITATION_NOT_FOUND`
- `OCR_LOW_CONFIDENCE`: 저신뢰 OCR 결과를 요청 실패(`422`)로 볼지, OCR 결과 검수 상태(`REVIEW_REQUIRED`, 아직 미구현)로 볼지는 Product·OCR·Frontend·Backend가 함께 결정해야 하는 별도 Decision 사안이다. 이번 PR에서는 HTTP status를 확정하지 않는다.
- `CITATION_NOT_FOUND`는 아직 승인되지 않은 출처 상세 조회 API를 전제하며, 기존 [Safety Result 계약 v1](./safety-result-v1.md)의 `fallback_code`(`NO_APPROVED_EVIDENCE` 등)와 의미가 겹칠 수 있어 해당 API 승인 시 함께 재검토한다.

가이드 생성 작업이 정상적으로 접수되었다는 뜻의 `202`는 오류가 아니라 성공 응답이므로 이 표에 두지 않습니다. Post-MVP-1에서 공통 비동기 Job 기반이 도입되면 `202 {"data": JobStatusResponse}` 형태로 접수되며, 세부 응답 형태는 [비동기 Job 계약 v1](./async-job-v1.md)을 따릅니다.

AI가 안전 제한이나 근거 부족으로 답변을 제한하는 경우는 오류 코드가 아니라 정상 응답의 상태 축으로 구분합니다. 확정된 상태 축은 [Safety Result 계약 v1](./safety-result-v1.md)을 따르며, 요약은 [복약 챗봇 Backend-AI Core 계약](./medication-chat-ai-backend.md)에 둡니다.

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
- HTTP 상태 코드와 사용자 메시지를 함께 정의합니다.
- 실제 사용 상황 예시를 문서에 추가합니다.
- 이 문서에 등록한 뒤 Backend 코드에 적용합니다. 아직 구현하지 않았다면 "Post-MVP" 표에 등록합니다.
