# 복약 챗봇 Backend–AI Core 계약

## 목적과 적용 범위

이 문서는 복약 챗봇 요청이 Backend DTO에서 Provider payload로 변환되고 결과와 오류가 다시 Backend로 전달되는 공유 경계를 정의한다. HTTP API body 계약은 [`docs/api.md`](../api.md), 상세 구현 설계는 [Backend 연동 설계](../designs/ceohwj/medication-chat-ai-backend-integration-design.md)를 따른다.

실제 구현 스키마의 기준은 `app.services.chat_ai`의 Backend DTO와 `app.services.chat_ai.schemas`의 AI Core 입력 모델이다. 이 문서는 두 경계 사이에서 보존하거나 제외해야 할 의미를 고정한다.

## Backend 입력 계약

`ChatService`는 활성 세션에 연결된 확정 처방의 약물을 `display_order` 오름차순으로 조회하고 `ChatReplyInput`을 만든다.

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `prescription_id` | `UUID` | Backend 조회·추적용이며 Provider에 전달하지 않는다. |
| `content` | `str` | 현재 요청의 질문 하나만 전달한다. |
| `medications` | `list[ChatMedicationInput]` | 확정 처방의 전체 약물을 순서대로 전달한다. |

`ChatMedicationInput`의 필드는 다음과 같다.

| 필드 | 타입 | 보존 규칙 |
| --- | --- | --- |
| `medication_name` | `str` | 필수 |
| `dose_value` | `Decimal \| None` | `float`로 변환하지 않는다. |
| `dose_unit` | `str \| None` | 값 또는 `None`을 보존한다. |
| `frequency_per_day` | `int \| None` | 값 또는 `None`을 보존한다. |
| `timing_text` | `str \| None` | 값 또는 `None`을 보존한다. |
| `duration_days` | `int \| None` | 값 또는 `None`을 보존한다. |

`display_order`는 Provider 필드가 아니라 약물 배열의 순서로 보존한다. 약물이 AI Core의 최대 개수인 30개를 초과하면 일부만 잘라 전달하지 않고 입력 검증 실패로 처리한다.

## Provider payload 계약

Provider에는 다음 정보만 전달할 수 있다.

- 현재 질문 `question`
- 확정 처방의 약물 배열 `medications`
- 약물별 허용 필드: `medication_name`, `dose_value`, `dose_unit`, `frequency_per_day`, `timing_text`, `duration_days`

`Decimal` 값은 정밀도를 잃지 않는 문자열로 JSON 직렬화한다. AI Core 규칙에 따라 `dose_value`와 `dose_unit` 중 하나만 존재하면 두 필드를 모두 생략한다. 값이 없는 선택 필드는 Provider JSON에서 생략할 수 있다.

다음 정보는 Provider payload에 포함하지 않는다.

- 사용자, 세션, 처방전, 의료문서 또는 메시지 식별자
- 이전 대화 기록
- 인증정보와 API key
- DB 저장 상태와 내부 오류 metadata

예시의 모든 값은 합성 데이터여야 하며 실제 환자정보를 사용하지 않는다.

## AI 결과 계약

성공 결과는 `ChatReplyOutput`으로 반환한다.

| 필드 | 의미 |
| --- | --- |
| `content` | Provider 응답의 구조·빈값 처리를 통과한 답변 본문. 의료 근거, Citation/NLI 또는 AI 품질 평가 통과를 의미하지 않는다. |
| `model_name` | Provider가 반환한 실제 모델 식별자 |
| `prompt_version` | AI Core가 사용한 프롬프트 버전 |

Backend는 세 값을 완료된 ASSISTANT 메시지에 저장하고 같은 값을 `201 Created` 응답에 사용한다.

## 오류 경계

Adapter와 Service의 오류 mapping은 다음 고정 계약을 따른다.

| AI Core·Provider 실패 | Backend 분류 | DB `error_code` | 고정 DB `error_message` | HTTP status·code | HTTP message·detail |
| --- | --- | --- | --- | --- | --- |
| 전체 timeout 또는 Provider timeout | `ChatTimeoutError` | `OPENAI_API_TIMEOUT` | `OpenAI 호출이 제한 시간 내에 완료되지 않았습니다.` | `504 GATEWAY_TIMEOUT` | `외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.` / `openai_api: OPENAI_API_TIMEOUT` |
| 연결·rate limit·Provider 가용성 실패 | `ChatServiceUnavailableError` | `OPENAI_API_ERROR` | `OpenAI 서비스 호출에 실패했습니다.` | `503 SERVICE_UNAVAILABLE` | `현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.` / `openai_api: OPENAI_API_ERROR` |
| 설정·입력 검증·응답 처리 실패 | `ChatGenerationFailedError` | `OPENAI_RESPONSE_PROCESSING_FAILED` | `챗봇 응답 생성 처리 중 오류가 발생했습니다.` | `500 AI_RESPONSE_FAILED` | `AI 답변 생성에 실패했습니다.` / `assistant_message: OPENAI_RESPONSE_PROCESSING_FAILED` |
| 그 밖의 예상하지 못한 `Exception` | Service의 안전한 fail-safe | `OPENAI_RESPONSE_PROCESSING_FAILED` | `챗봇 응답 생성 처리 중 오류가 발생했습니다.` | `500 AI_RESPONSE_FAILED` | `AI 답변 생성에 실패했습니다.` / `assistant_message: OPENAI_RESPONSE_PROCESSING_FAILED` |

Provider와 AI Core의 원본 예외 chain 및 본문은 API 응답, 저장 metadata와 로그에 노출하지 않는다. 오류 분류만 `except` 안에서 수행하고, 실패 쌍 commit과 안전한 `ApiError` 발생은 `except` 밖에서 수행한다.

실패 시 USER 메시지와 FAILED ASSISTANT 메시지를 한 쌍으로 보존한다. FAILED ASSISTANT의 `content`, `model_name`, `prompt_version`은 `NULL`이며, `error_code`와 `error_message`에는 위 표의 안전한 고정값만 저장한다.

DB lock wait timeout처럼 세션 row lock을 얻기 전에 발생한 DB 오류는 위 AI 오류 mapping 대상이 아니다. 공통 `500 INTERNAL_SERVER_ERROR`를 반환하고 USER·ASSISTANT 메시지와 실패 metadata를 새로 저장하지 않는다.

## 동시성과 캐시 관찰 계약

- 동일 세션의 메시지 전송은 `CHAT_SESSION` row의 `SELECT ... FOR UPDATE`로 직렬화한다.
- 성공한 USER·ASSISTANT 쌍의 `message_seq`는 연속하며 동일 세션에서 중복되지 않는다.
- 잠금 획득 전 DB lock timeout이 발생하면 새 메시지를 저장하지 않는다.
- 모든 채팅 API 성공·오류 응답에는 `Cache-Control: no-store`를 포함한다.
- 공통 오류 body, `ApiError.headers`, `WWW-Authenticate`와 CORS 동작은 기존 HTTP 계약을 유지한다.

## 검증과 변경 규칙

구현 계약은 `tests/contract/test_chat_ai_backend_contract.py`에서 Provider payload와 결과 metadata를 검증한다. API·DB·동시성 관찰 결과는 `app/tests/chat_apis`, `app/tests/chat_integration`과 repository 테스트에서 검증한다.

다음 변경은 이 문서, 구현, API 문서와 관련 계약·통합 테스트를 같은 PR에서 갱신해야 한다.

- 공유 필드의 추가·삭제·이름·타입·필수 여부 변경
- Provider 허용 또는 금지 데이터 범위 변경
- 오류 분류, HTTP status, code 또는 message 의미 변경
- 실패 메시지 persistence 또는 동시성 의미 변경
- API 응답 body나 cache/header 계약 변경

별도 배포·버전 관리되는 외부 소비자가 생기면 현재 저장소 내부 계약으로 간주하지 말고 versioned contract와 호환 정책을 관련 CODEOWNERS와 먼저 합의한다.
