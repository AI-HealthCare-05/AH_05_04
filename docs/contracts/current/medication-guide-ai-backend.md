# 복약 가이드 Backend–AI 계약

> **적용 구분:** 이 문서는 현재 구현된 동기 one-cycle의 Python 모듈 경계다. [비동기 Job 계약 v1](../targets/post-mvp-1/async-job-v1.md)과 [Outbox·Stream 계약 v1](../targets/post-mvp-1/outbox-stream-v1.md)은 승인된 Post-MVP-1 목표이며 아직 현재 HTTP 동작을 대체하지 않는다. Worker 전환 시 이 문서의 입력·출력·Provider 오류 변환 경계를 재사용한다.

| 항목 | 내용 |
| --- | --- |
| 상태 | Implemented |
| 관련 Issue | #11, #48 |
| 검토 CODEOWNER | `@hazelnutflavoured`, `@phina-io`, `@ceohwj` |
| 구현 | `backend/app/services/guide_ai/`, `backend/app/services/guides.py` |

이 문서는 동기 One Cycle 복약 가이드 생성에서 Backend와 Guide AI 모듈이 공유하는 Python 경계를 기록한다. HTTP 요청·응답, DB 모델과 GUIDE 상태 전이는 기존 Backend 계약을 따르며 이 문서가 새 필드나 상태를 추가하지 않는다.

## 책임 경계

Backend는 인증, 처방 소유권과 확정 상태 확인, 영속 약물 조회, GUIDE 생성·완료·실패 저장, HTTP 오류 변환을 담당한다. Guide AI 모듈은 전달받은 확정 약물 정보로 provider를 호출하고, 구조화 출력을 검증한 뒤 원본 처방값과 결합한 `GuideGenerationResult`를 반환한다.

`GuideGenerator`는 DB, FastAPI 객체와 GUIDE 상태를 참조하지 않는다. `OpenAIResponsesClient`만 OpenAI SDK 타입과 예외를 알고, `GuideGenerator`에는 `GuideProvider`, 모델명과 전체 제한시간을 주입한다. Guide AI와 Chat AI는 서로의 스키마·provider 계약을 공유하지 않는다.

## 입력 계약

Backend는 한 개 이상의 확정 약물을 `GuideGenerationInput.medications`에 전달한다. Guide AI는 전달받은 순서대로 0-based `source_index`를 부여하고 최종 평문에서도 그 순서를 유지한다. `Prescription.medications` relationship에 `MEDICATION.display_order` 명시 정렬이 적용되어 있어, Backend 조회 시점부터 처방 표시 순서가 보장된다.

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `medication_name` | `str` | 필수 약명 |
| `dose_value` | `Decimal \| None` | 선택 양수 용량 값 |
| `dose_unit` | `str \| None` | 선택 용량 단위 |
| `frequency_per_day` | `int \| None` | 선택 양수 일일 횟수 |
| `timing_text` | `str \| None` | 선택 복용 시점 |
| `duration_days` | `int \| None` | 선택 양수 기간 |
| `strength_text` | `str \| None` | 선택 제품 함량, 최대 100자 |

환자·사용자·처방 식별자, OCR 원문, 이미지와 미확정 값은 Guide AI 입력에 포함하지 않는다. 입력 문자열은 NFC 정규화, 앞뒤 공백 제거와 연속 공백 축약을 거치며 NUL, bidi override와 zero-width 문자는 거부한다.

용량 값과 단위 중 하나만 존재하면 최종 평문에서 둘 다 생략하고 고정 확인 안내를 표시한다. 확정 처방값은 provider payload에 포함하지 않는다. 입력 모델 검증이 실패하면 provider를 호출하지 않으며 현재 Backend는 이를 일반 생성 실패 경로로 처리한다.

## Provider 호출 계약

`GuideProvider.generate()`는 다음 keyword-only 값을 받는다.

- `model`
- `instructions`
- `input_json`
- `max_output_tokens`

`input_json`에는 입력 순서를 연결하기 위한 0-based `source_index`만 포함합니다.

```json
[
  { "source_index": 0 },
  { "source_index": 1 }
]
```
약명·제품 함량·복용량·단위·횟수·복용 시점·기간을 포함한 확정 처방값은 `Backend renderer`에만 남기며 `provider`에 전달하지 않는다.
OpenAI adapter는 비스트리밍 `responses.parse`, `text_format=GeneratedGuideDraft`, `store=False`를 사용한다. `max_output_tokens`는 `400 + (160 × 약물 수)`이며, `GuideGenerator`가 provider 호출 전체를 주입된 timeout으로 제한한다.

## 출력 계약

성공 시 `GuideGenerator.generate()`는 다음 `GuideGenerationResult`를 반환한다.

| 필드 | 계약                                                             |
| --- |------------------------------------------------------------------|
| `content` | 검증된 AI 안내와 원본 처방값을 결합한 10,000자 이하 UTF-8 평문   |
| `model_name` | provider 응답에서 확인한 비어 있지 않은 실제 모델 ID, 100자 이하 |

| `prompt_version` | 현재 `guide-prompt-v2`                                           |

`content`는 OpenAI의 원문이나 `response.output_text`가 아니다. 약명·용량·횟수·복용 시점·기간은 원본 `MedicationInput`에서 결정론적으로 렌더링하고, AI 출력에서는 `source_index`, 검증된 `guidance`와 `general_notice`만 사용한다. 입력 약물 순서와 각 `source_index`의 대응을 유지한다.

Backend는 성공 결과의 세 필드를 GUIDE 완료 저장에 사용하며, 저장한 `content`와 성공 API 응답의 `content`를 동일하게 유지한다.

## 오류 계약

OpenAI SDK 예외는 `OpenAIResponsesClient` 밖으로 노출하지 않는다.

| AI 오류 | 의미 | 현재 Backend 처리 |
| --- | --- | --- |
| `GuideGenerationInputError` | 향후 Backend 입력 변환 경계를 위한 호환성 예약 예외이며 현재 발생 경로 없음 | 해당 없음 |
| `GuideGenerationTimeoutError` | 전체 provider 호출 또는 SDK timeout | GUIDE 실패, `504 GATEWAY_TIMEOUT` |
| `GuideGenerationUnavailableError` | 연결·rate limit·408·409·5xx | GUIDE 실패, `503 SERVICE_UNAVAILABLE` |
| `GuideGenerationConfigurationError` | OpenAI SDK가 HTTP `4xx`로 반환한 인증·권한·모델 또는 설정 오류 | 일반 생성 실패 |
| `GuideGenerationInvalidResponseError` | 불완전·빈·다중·파싱 불가 응답 또는 잘못된 실제 모델 ID | 일반 생성 실패 |
| `GuideGenerationSafetyError` | refusal, content filter, 처방 항목 불일치 또는 기본 안전 규칙 위반 | 일반 생성 실패 |

예상하지 않은 프로그래밍 오류와 cancellation은 provider 장애로 숨기지 않는다. Backend가 저장하는 오류 메시지는 provider 응답이나 처방 데이터를 포함하지 않는 고정 문구를 사용한다.

## 조립 계약

Backend 조립부는 process-scoped `AsyncOpenAI(max_retries=0)`을 만들고 종료 시 닫는다. `OPENAI_MODEL`과 양수 `OPENAI_TIMEOUT_SECONDS`를 `GuideGenerator`에 주입한다. API Key는 환경변수로 관리하며 코드, fixture와 로그에 포함하지 않는다.

프롬프트, 출력 스키마, validator 또는 renderer 변경으로 사용자 표시 결과가 달라지면 `prompt_version`을 올리고 관련 계약·테스트를 함께 검토한다. 이 계약의 필드·타입·오류 의미를 바꾸는 작업은 Backend와 AI CODEOWNER의 합의가 필요하다.
