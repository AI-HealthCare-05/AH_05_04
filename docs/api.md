# API 명세

## 공통 규칙

- Base path: `/api/v1`
- 요청·응답 형식과 오류 코드는 팀 공통 규칙을 따릅니다.
- 인증이 필요한 API는 권한 조건을 함께 기록합니다.

## 공통 오류 응답 형식

모든 API의 오류 응답은 아래 형식(`app/core/errors.py`)을 따릅니다.

```json
{
  "code": "string",
  "message": "string",
  "details": [
    { "field": "string", "reason": "string", "rejected_value": null }
  ],
  "trace_id": "string"
}
```

- `trace_id`는 요청별 미들웨어(`app/main.py`)가 생성해 `request.state.trace_id`에 저장하고, 모든 에러 핸들러가 이 값을 재사용합니다(핸들러가 자체적으로 새 값을 만들지 않음). 성공 응답 body에는 아직 포함하지 않으며, 필요 시 로그·감사로그와 연결할 수 있도록 모든 요청에서 `request.state`에 존재합니다.
- 기존 `HTTPException` 기반 코드(`{"detail": "..."}`)도 전역 핸들러가 위 형식으로 자동 변환합니다. 이때 `code`는 `HTTP_ERROR`로 고정되고 `message`에 원래 `detail` 값이 들어갑니다.
- 예상치 못한 예외는 `code: INTERNAL_SERVER_ERROR`, 500으로 변환되며 내부 오류 내용은 노출하지 않습니다.

## CORS

- Backend API 서버는 로컬 개발 기준 `http://localhost:8000`에서 실행합니다.
- Frontend 개발 서버 origin은 `http://localhost:5173`으로 사용합니다.
- Frontend는 `VITE_API_BASE_URL=http://localhost:8000`으로 Backend API를 호출합니다.
- Backend는 `CORS_ALLOWED_ORIGINS=http://localhost:5173`을 허용 origin으로 사용합니다.
- `CORSMiddleware`가 `CORS_ALLOWED_ORIGINS` 환경변수(콤마로 구분된 origin 목록)를 기준으로 허용 origin을 관리합니다.

## API 목록

현재 등록된 Router endpoint의 요약입니다. 상세 요청·응답과 오류 의미는 관련 계약 문서와 FastAPI OpenAPI를 기준으로 합니다.

| 영역 | Method | Path | 성공 상태 |
| --- | --- | --- | ---: |
| 인증 | `POST` | `/api/v1/auth/signup` | `201` |
| 인증 | `POST` | `/api/v1/auth/login` | `200` |
| 인증 | `GET` | `/api/v1/auth/token/refresh` | `200` |
| 사용자 | `GET` | `/api/v1/users/me` | `200` |
| 사용자 | `PATCH` | `/api/v1/users/me` | `200` |
| 의료문서 | `POST` | `/api/v1/documents` | `201` |
| OCR 실행 | `POST` | `/api/v1/documents/{document_id}/ocr-jobs` | `202` |
| 처방 확정 | `POST` | `/api/v1/documents/{document_id}/prescription` | `201` |
| 의료문서 | `GET` | `/api/v1/documents/{document_id}/file` | `200` |
| OCR | `GET` | `/api/v1/ocr-jobs/{job_id}` | `200` |
| OCR 검수 | `PATCH` | `/api/v1/extracted-fields/{field_id}` | `200` |
| 처방 | `GET` | `/api/v1/prescriptions/{prescription_id}` | `200` |
| 채팅 | `POST` | `/api/v1/prescriptions/{prescription_id}/chat-sessions` | `201` |
| 가이드 | `POST` | `/api/v1/guides` | `201` |
| 가이드 | `GET` | `/api/v1/guides/{guide_id}` | `200` |
| 채팅 | `GET` | `/api/v1/chat-sessions/{session_id}/messages` | `200` |
| 채팅 | `POST` | `/api/v1/chat-sessions/{session_id}/messages` | `201` |

OCR 실행 endpoint는 `202 Accepted`를 반환하지만 현재 구현은 비동기 queue 접수가 아닙니다. 같은 HTTP 요청에서 CLOVA OCR 호출과 결과 저장을 완료합니다.

## 복약 챗봇

### Endpoint

| Method | Path | 성공 상태 | 동작 |
| --- | --- | ---: | --- |
| `POST` | `/api/v1/prescriptions/{prescription_id}/chat-sessions` | `201 Created` | 확정 처방에 대한 활성 채팅 세션을 생성합니다. |
| `GET` | `/api/v1/chat-sessions/{session_id}/messages` | `200 OK` | 세션의 USER·ASSISTANT 메시지를 순서대로 조회합니다. |
| `POST` | `/api/v1/chat-sessions/{session_id}/messages` | `201 Created` | USER 메시지 저장, AI 응답 생성, ASSISTANT 메시지 저장을 한 요청에서 완료합니다. |

위 세 endpoint의 모든 성공·오류 응답은 `Cache-Control: no-store`를 포함합니다. Router endpoint를 실행하지 않고 최외곽 CORS middleware가 직접 처리하는 preflight 응답은 이 정책의 대상이 아닙니다.

### 메시지 전송

요청 body는 변경되지 않습니다.

```json
{
  "content": "합성 질문"
}
```

이 endpoint는 streaming이나 별도 결과 조회를 사용하지 않는 동기 one-cycle 계약을 유지합니다. AI 응답과 저장이 완료된 뒤 다음 기존 body를 `201 Created`로 반환합니다.

```json
{
  "data": {
    "user_message_id": "11111111-1111-4111-8111-111111111111",
    "assistant_message_id": "22222222-2222-4222-8222-222222222222",
    "session_id": "33333333-3333-4333-8333-333333333333",
    "generation_status": "COMPLETED",
    "content": "합성 답변",
    "model_name": "synthetic-model",
    "prompt_version": "chat-prompt-v1",
    "created_at": "2026-08-21T10:00:00Z",
    "completed_at": "2026-08-21T10:00:01Z"
  }
}
```

AI 생성 오류는 [공통 오류 응답 형식](#공통-오류-응답-형식)을 사용합니다.

| 상태 | `code` | `details.field` | `details.reason` |
| ---: | --- | --- | --- |
| `500` | `AI_RESPONSE_FAILED` | `assistant_message` | `OPENAI_RESPONSE_PROCESSING_FAILED` |
| `503` | `SERVICE_UNAVAILABLE` | `openai_api` | `OPENAI_API_ERROR` |
| `504` | `GATEWAY_TIMEOUT` | `openai_api` | `OPENAI_API_TIMEOUT` |

생성이 실패해 HTTP 오류를 반환해도 해당 USER 메시지와 `generation_status: FAILED`인 ASSISTANT 메시지를 한 쌍으로 저장합니다. 뒤이은 메시지 목록 조회는 USER의 질문과 `content: null`인 FAILED ASSISTANT를 함께 반환합니다.

### AI 데이터 경계

AI에는 현재 요청의 질문과 해당 세션에 연결된 확정 처방의 약물 정보만 전달합니다. 사용자·세션·처방·메시지 식별자, 이전 대화, 처방전 이미지, OCR 원문과 미검수 데이터는 AI 경계를 넘지 않습니다.

### 동시 전송과 대기시간

같은 세션의 메시지 전송은 세션 row lock으로 직렬화합니다. 두 요청이 동시에 시작한 참고 시나리오에서 두 번째 요청의 지연은 `2 × T + M`입니다. `T`는 배포 환경의 OpenAI 전체 timeout, `M`은 애플리케이션 처리 여유입니다. 이 값은 참고 지연이지 최대 대기시간 계약이 아닙니다.

같은 세션에 세 개 이상의 요청이 겹치면 뒤 요청은 앞선 요청 수에 비례해 더 오래 대기합니다. 현재 설계는 동시 요청 수를 제한하지 않으므로 세 개 이상에 대한 유한한 end-to-end 최대시간을 보장하지 않습니다.

DB lock wait timeout이 발생하면 공통 `500 INTERNAL_SERVER_ERROR`를 반환합니다. 잠금을 얻어 USER·ASSISTANT를 만들기 전에 transaction이 rollback되므로 새 메시지가 생성되지 않으며, 메시지 목록을 다시 조회해도 이전 결과와 같습니다.

## OCR 결과 조회

### Endpoint

| Method | Path | 성공 상태 | 동작 |
| --- | --- | ---: | --- |
| `GET` | `/api/v1/ocr-jobs/{job_id}` | `200 OK` | 저장된 OCR 작업과 추출 필드를 조회합니다. |

### 약품명 필드 응답

`MEDICATION_NAME` 필드는 OCR 원문, 정규화 참고값 및 사용자 확정값을 함께 반환합니다.

```json
{
  "field_id": "11111111-1111-4111-8111-111111111111",
  "field_type": "MEDICATION_NAME",
  "medication_index": 1,
  "raw_value": "복합정 500 mg / 5 mg",
  "normalized_value": "복합정 500mg/5mg",
  "confirmed_value": null,
  "confidence_score": 0.99,
  "confirmation_status": "UNCONFIRMED",
  "normalization_version": "rule-v1"
}
```

- `raw_value`는 OCR이 인식한 원문이다.
- `normalized_value`는 표기 정리용 참고값이다.
- `confirmed_value`는 사용자가 확인하거나 수정한 최종 기준값이다.
- `normalized_value`는 자동 처방 확정이나 의약품 동일성 판단에 사용하지 않는다.
- 최종 처방에는 `confirmed_value`만 사용한다.


## 변경 이력

API 계약이 변경되면 관련 Issue와 Pull Request를 기록합니다.

| 날짜 | 관련 Issue/PR | 변경 내용 |
| --- | --- | --- |
| 2026-08-21 | Issue #51 / PR #52 | OCR 결과 조회 응답에 `normalized_value`와 `normalization_version`을 추가하고, `raw_value`, `normalized_value`, `confirmed_value`의 역할을 명시 |
