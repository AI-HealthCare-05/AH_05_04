# Local Live Provider 호출 증적 계약

## 범위

이 계약은 비식별 합성 fixture를 사용하는 `local-live-full` one-cycle에만 적용합니다. staging·production Live 검증, 배포 설정, 공개 API body, DB schema, Provider retry·생성 동작은 변경하지 않습니다. 실제 Live 실행과 Provider 로그 수동 검토는 자동 테스트가 아니라 별도 승인 작업입니다.

## 요청과 응답 상관관계

- runner는 one-cycle UUID를 `X-Validation-Run-Id`로 모든 요청에 보냅니다.
- Backend는 `ENV=local`, `RELEASE_VALIDATION_ALLOWED=true`일 때만 유효 UUID를 수용합니다.
- UUID 형식 오류는 `400 HTTP_ERROR`와 `Invalid validation run ID.`, 미승인 환경은 `403 HTTP_ERROR`와 `Validation run is not allowed.`를 반환합니다.
- Backend는 외부 trace Header를 신뢰하지 않고 요청마다 32자리 hexadecimal `trace_id`를 생성합니다.
- 모든 HTTP 응답은 `X-Trace-Id`를 반환하며 오류 body의 `trace_id`와 같습니다.
- `X-Trace-Id`는 CORS exposed header입니다. Nginx는 요청 validation Header와 응답 trace Header를 제거하거나 덮어쓰지 않습니다.
- `run_id`와 `trace_id`는 인증·인가·소유권·DB 조회 조건·Provider metadata가 아닙니다.

## Provider 컨텍스트와 descriptor

`ProviderCallContext`는 서버 생성 `trace_id`, nullable UUID `validation_run_id`, Backend `environment`, `validation_enabled`만 가집니다. `ProviderCallDescriptor`는 `provider`, 승인된 `operation`, nullable `prompt_version`만 가집니다. FastAPI `Request`, payload, 예외 객체는 Provider logger 직렬화 경계로 전달하지 않습니다.

Provider는 `CLOVA_OCR`, `OPENAI`만 허용합니다. operation은 다음 네 값만 허용합니다.

- `PRESCRIPTION_RECOGNITION`
- `OCR_STRUCTURING`
- `GUIDE_GENERATION`
- `CHAT_GENERATION`

## `provider-call-log-v1`

Provider 네트워크 어댑터는 실제 호출 직전에 `provider.call.started`를 한 줄 JSON으로 기록하고 Provider 응답 상태와 Schema 파싱 후 `provider.call.succeeded` 또는 `provider.call.failed`를 최대 한 건 기록합니다. logger는 prefix 없는 `%(message)s`, propagation 비활성, 한 record 한 줄을 사용합니다. 로그 실패는 Provider 결과를 바꾸거나 재호출하지 않고 고정 경고 `provider_log_emit_failed=true`만 best effort로 남깁니다.

허용 필드는 다음 20개뿐입니다.

`schema_version`, `event`, `occurred_at`, `environment`, `validation_run_id`, `trace_id`, `provider_call_id`, `provider`, `operation`, `requested_model`, `model_name`, `prompt_version`, `provider_request_id`, `provider_response_id`, `provider_response_received`, `http_status`, `latency_ms`, `outcome`, `failure_phase`, `error_code`.

일반 요청은 `validation_run_id`, `provider_request_id`, `provider_response_id`를 `null`로 기록합니다. Provider request·response ID는 승인된 local validation 요청에서 안전하게 확인된 경우에만 기록합니다. 성공 SDK 객체에 HTTP status가 없으면 `200`을 추정하지 않습니다. latency는 monotonic clock의 정수 millisecond이고 terminal에만 있습니다.

실패 단계는 `TRANSPORT_TIMEOUT`, `TRANSPORT_CONNECTION`, `HTTP_STATUS`, `RESPONSE_VALIDATION`, `PROVIDER_POLICY`, `APPLICATION_DEADLINE`, `UNKNOWN_INTERNAL`만 허용합니다. 안전 오류 code는 `PROVIDER_TIMEOUT`, `PROVIDER_CONNECTION_FAILED`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `PROVIDER_REQUEST_REJECTED`, `PROVIDER_RESPONSE_INVALID`, `PROVIDER_REFUSAL`, `PROVIDER_SAFETY_FILTERED`, `PROVIDER_CALL_ABORTED`, `PROVIDER_INTERNAL_FAILURE`만 허용합니다. cancellation terminal을 best effort로 기록한 뒤 `CancelledError`를 다시 전파합니다.

Provider span은 외부 호출과 Provider Schema 파싱까지만 포함합니다. 이후 Grounding·의료 안전·정규화·renderer 검증은 terminal을 변경하지 않습니다.

## 금지정보

API Key, CLOVA Secret, Authorization·Provider 인증 Header, 이미지 byte·파일명·경로·object key, OCR 원문, 약명·용량 등 의료정보, 질문·답변, 사용자·Profile·Document·Prescription 식별자, Provider payload, instructions·Prompt 본문, SDK 예외 메시지·객체 표현을 로그와 증빙에 기록하지 않습니다.

## runner와 최종 판정

`local-live-full` runner만 validation Header와 trace 강제 검증을 사용합니다. trace 누락·비hex·오류 body 불일치는 실행 실패입니다. `provider_traces`는 CLOVA와 활성화된 OCR 구조화, Guide, Chat의 요청 trace를 기록합니다. OCR 구조화 비활성은 `SKIPPED`, `OCR_STRUCTURE_LLM_DISABLED`, `trace_id=null`입니다.

OCR 구조화 활성 경로는 DB `model_version`·`prompt_version`과 `OCR_STRUCTURING` 로그가 모두 있어야 하고 비활성 경로는 모두 없어야 합니다. runner 결과는 API·DB·cleanup 성공 시에도 `provider_log_verification=MANUAL_REQUIRED`를 유지합니다.

전체 증빙은 동일 `run_id`의 다음 세 Artifact와 지정 검토자 수동 판정으로 구성합니다.

- `one-cycle-result.json`
- `provider-call-log-<run_id>.jsonl`
- `provider-log-review-<run_id>.json`

`execution=PASS`, `database_verification=PASS`, `cleanup=PASS`, 수동 review `result=PASS`를 모두 만족해야 완료입니다. Artifact는 저장소에 commit하지 않고 접근 제한 위치에 보관한 뒤 팀 보존 정책에 따라 삭제합니다.
