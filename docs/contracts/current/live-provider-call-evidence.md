# Local Live Provider 호출 증적 계약

## 범위

이 계약의 범위는 다음과 같이 분리하여 적용합니다.

1. **공통 범위 (`local-preflight` 및 `local-live-full`)**:
   - 사전 GUARD, 로그인 성공 후 문서 업로드 및 OCR 접수 전에 반드시 Issue #458 승인 동의 게이트인 `POST /api/v1/users/me/consents/OCR`를 호출하는 실행 순서 및 fail-closed 검증 규약.
   - 고정 정책 버전 `ocr-local-synthetic-demo-2026-09-14-v1`은 Issue #152 로컬 비식별 합성 실행 전용 폐쇄 allowlist이며, 향후 정책 버전 변경 시 코드와 계약을 함께 갱신해야 합니다.
2. **`local-live-full` 전용 범위**:
   - 비식별 합성 fixture를 사용하는 전체 live one-cycle의 요청별 validation header(`X-Validation-Run-Id`), 서버 생성 `trace_id`(`X-Trace-Id`) 수집·일치 검증, Provider call log(`provider-call-log-v1`) 발췌, DB 결과(`llm_processing == "NOT_REQUESTED"` 등) 검증 및 수동 Provider 로그 판정 증빙.
   - staging·production Live 검증, 배포 설정, 공개 API body, DB schema, Provider retry·생성 동작은 변경하지 않습니다. 실제 Live 실행과 Provider 로그 수동 검토는 자동 테스트가 아니라 별도 승인 작업입니다.

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

Provider observability는 기본적으로 활성입니다. 활성 상태에서는 context와 descriptor를 모두 전달해야 하며 누락을 로그 0건으로 조용히 처리하지 않습니다. 테스트를 제외한 runtime `observability_disabled=true` allowlist는 local release evidence 범위 밖의 독립 평가 진입점 `backend.app.evaluation.chat_history_runner.execute` 하나뿐이며, 이 상태에는 context나 descriptor를 함께 전달하지 않습니다. 새 runtime opt-out은 이 current contract와 관련 회귀 테스트를 함께 갱신하고 Backend/API·Security 및 영향 도메인 리뷰를 받아야 합니다. HTTP dependency wiring은 opt-out하지 않습니다.

Provider observability data contract는 `provider_contracts.observability`에 있습니다. Backend `Env`는 `DeploymentEnvironment`의 alias이고, 기존 `app.core.provider_observability` import 경로는 공용 Provider context·descriptor·enum을 다시 노출합니다. 공용 package는 Backend·Worker 설정, DB, FastAPI, logger를 import하지 않습니다.

현재 Worker에는 Provider adapter 조립 경로가 없고 #141도 OCR·Guide·Chat Handler 비즈니스 로직을 제외합니다. Worker는 `create_worker_provider_call_context()`로 검증된 message trace와 Worker `Config.ENV`를 사용해 context를 만듭니다. 호출자는 `Config.ENV`를 factory의 `environment`에 그대로 전달해야 합니다. 이 factory는 설정·logger를 초기화하거나 Backend를 import하지 않으며, 현재 비동기 계약에서는 `validation_run_id=null`, `validation_enabled=false`로 고정합니다. Worker `Config.ENV`는 `local`, `staging`, `production` 중 하나를 명시해야 하며 production compose는 `ENV`를 Worker에 전달합니다. 실제 Provider adapter 조립은 후속 Handler 구현의 책임입니다. Worker에 Backend DB credential을 주입하거나 Provider observability 타입을 중복 정의하거나 Provider adapter를 opt-out 상태로 조립하지 않습니다.

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

`local-live-full` 실행 경계에 진입한 결과는 성공과 실패 모두 `execution_mode=LIVE`를 기록합니다. `database_verification`은 실제 검증 단계에 따라 `NOT_RUN|FAIL|PASS`, `provider_log_verification`은 trace가 있으면 `MANUAL_REQUIRED`, 없으면 `UNVERIFIED`입니다. 수행하지 않은 검증을 `PASS`로 기록하지 않으며 Provider 로그 판정도 자동으로 `PASS`가 되지 않습니다.

실패 Artifact는 공개 오류 body의 `details.reason` 중 `DEADLINE_EXCEEDED|PROVIDER_TIMEOUT`만 runner 전용 `failure_evidence.api_reason`으로 복사할 수 있습니다. 다른 reason과 `details`의 나머지 내용은 기록하지 않습니다.

### OCR 동의 게이트 연결 및 실패 단계

- AGENTS.md 규칙에 따라, `OCR_CONSENT` failure_stage enum 추가 및 canonical runner 실행 순서는 [#152 로컬 라이브 검증 OCR 동의 게이트 연결 결정안](../../governance/decisions/2026-09-15-local-live-ocr-consent-152.md)에 근거하여 연결된 규약입니다. 본 변경의 최종 승인은 AI 어시스턴트의 자체 판정이 아니며, 단일 책임 리뷰어인 송은영(@phina-io)의 PR 코드 및 문서 리뷰 승인을 통해 완료됩니다.
- `local-live-full` 및 `local-preflight` runner는 로그인 성공 후 문서 업로드 및 OCR 접수 전에 반드시 Issue #458 승인 동의 게이트인 `POST /api/v1/users/me/consents/OCR`를 호출합니다.
- 필수 실행 순서는 사전 GUARD → fixture 준비 → 로그인 → OCR 동의 → 이미지 업로드 → OCR 실행 → 후속 검증입니다.
- 요청 본문의 `policy_version`은 승인된 정책 버전 `ocr-local-synthetic-demo-2026-09-14-v1`(`OCR_CONSENT_POLICY_VERSION`)에서 전달받으며 임의 기본값을 허용하지 않습니다. 이 버전은 Issue #152 로컬 비식별 합성 실행 전용 폐쇄 allowlist이며, 향후 정책 버전 변경 시 코드와 계약을 함께 갱신해야 합니다.
- 동의 API 응답이 수신되면 runner의 in-flight request 상태를 즉시 완료 처리(`_complete_request`)하여 이후 단계 실패 시 cleanup이 `PENDING`으로 남지 않도록 보장합니다.
- 동의 응답 검증(status `GRANTED`, `effective is True`, 정책 버전 일치 등) 실패 또는 동의 API 오류(409, 503 등) 시 failure stage는 `OCR_CONSENT`로 기록되며, 문서 업로드나 외부 Provider 호출 없이 즉시 fail-closed 처리됩니다.
- 환경 검증 단계에서 정책 버전 누락·형식 불일치 또는 `OCR_STRUCTURE_LLM_ENABLED!=false`인 경우 failure stage는 `GUARD`이며, DB fixture나 상태 파일을 생성하지 않고 즉시 fail-closed 처리됩니다.
- runner 프로세스 환경에는 `OPENAI_API_KEY`와 `CLOVA_OCR_SECRET`이 주입되지 않습니다.
- `local-live-full` DB 검증 단계에서 OCR Job의 `llm_processing` 상태를 필수로 검증합니다:
  - `ocr_structuring_expected=False`인 경우: 반드시 `llm_processing == "NOT_REQUESTED"`, `model_version is None`, `prompt_version is None`이어야 합니다. Backend/Worker에서 기능이 활성화되었으나 최소화 정책으로 생략된 `SKIPPED_MINIMIZATION` 상태는 이번 실행 증거로 인정하지 않고 `DB_VERIFICATION` 실패로 처리합니다.
  - `ocr_structuring_expected=True`인 경우: 반드시 `llm_processing == "APPLIED"`, `model_version` 및 `prompt_version`이 모두 존재해야 합니다. `SKIPPED_MINIMIZATION`, `NOT_REQUESTED`, `None`은 활성 실행 증거로 인정하지 않고 `DB_VERIFICATION` 실패로 처리합니다.
  - 반환되는 `ocr_database` evidence에도 `llm_processing`을 포함합니다.

전체 증빙은 동일 `run_id`의 다음 세 Artifact와 지정 검토자 수동 판정으로 구성합니다.

- `one-cycle-result.json`
- `provider-call-log-<run_id>.jsonl`
- `provider-log-review-<run_id>.json`

`execution=PASS`, `database_verification=PASS`, `cleanup=PASS`, 수동 review `result=PASS`를 모두 만족해야 완료입니다. Artifact는 저장소에 commit하지 않고 접근 제한 위치에 보관한 뒤 팀 보존 정책에 따라 삭제합니다.
