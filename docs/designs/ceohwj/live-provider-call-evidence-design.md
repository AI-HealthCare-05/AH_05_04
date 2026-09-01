# OpenAI·CLOVA Live Provider 호출 증적 설계

## 문서 상태

- 대상 Issue: `#152`
- 작업 브랜치: `task152/live-provider-call-evidence`
- 상태: 구현 전 확정 설계
- 작성일: 2026-09-01
- 구현 범위: Backend Provider 관측, 요청 trace, one-cycle Live 검증 결과
- 구현 제외: 별도 로그 DB, 외부 로그 플랫폼, Provider payload 저장

## 배경

멘토링 과정에서 Mock 테스트 결과가 아니라 CLOVA OCR과 OpenAI API가 실제로 호출됐음을 확인할 수 있는 실행 증거가 필요하다는 피드백을 받았습니다.

현재 저장소에는 다음 기반이 이미 있습니다.

- `local-live-full` one-cycle 검증 모드
- 검증 실행별 UUID `run_id`
- 별도 TCP 연결을 강제하는 `NetworkOneCycleRunner`
- OCR·Guide·Chat 결과의 DB 검증
- 비식별 합성 Fixture와 실행 후 cleanup
- 요청별 서버 생성 `trace_id`
- OCR·Guide·Chat DB의 엔진·모델·프롬프트 버전

하지만 현재 `trace_id`는 주로 오류 응답에만 노출되고, `run_id`가 Backend 요청과 Provider 호출까지 전달되지 않습니다. CLOVA·OpenAI 어댑터에도 동일 실행을 연결할 수 있는 구조화된 시작·성공·실패 로그가 없습니다. 따라서 DB 결과만으로는 실제 Provider 호출과 동일 실행을 직접 연결하기 어렵습니다.

## 목표

승인된 환경에서 팀원이 비식별 합성 데이터로 one-cycle Live 검증을 실행한 뒤 다음을 확인할 수 있게 합니다.

1. CLOVA OCR 실제 호출
2. 활성화된 경우 OCR 구조화 OpenAI 실제 호출
3. Guide OpenAI 실제 호출
4. Chat OpenAI 실제 호출
5. Provider 호출과 동일 요청의 `trace_id` 연결
6. 전체 one-cycle의 `run_id`로 Backend 로그 검색
7. OCR·Guide·Chat DB 저장 결과 검증
8. 합성 데이터 cleanup 확인
9. Live 실행과 Mock 실행 구분
10. Docker Desktop 또는 Docker CLI에서 쉬운 로그 조회

## 비목표

- 로그 원문을 one-cycle 최종 JSON에 복제하지 않습니다.
- 별도 로그 DB table을 만들지 않습니다.
- 외부 로그·APM 플랫폼을 도입하지 않습니다.
- 운영 provenance table을 만들지 않습니다.
- Provider 요청·응답 payload를 저장하지 않습니다.
- OCR·Guide·Chat 생성 동작과 retry 정책을 변경하지 않습니다.
- 공개 API response body를 변경하지 않습니다.
- Docker stdout의 장기 보존을 보장하지 않습니다.

## 핵심 선택

### `run_id`와 서버 `trace_id`를 함께 사용합니다

one-cycle 검증기가 `X-Validation-Run-Id`를 전송하고 Backend는 기존 방식대로 요청마다 `trace_id`를 생성합니다. Provider 로그에는 두 값을 함께 기록합니다.

- `run_id`: one-cycle 전체 실행 조회
- `trace_id`: 단일 HTTP 요청 조회
- `provider_call_id`: 하나의 Provider 시작·terminal 이벤트 연결

최종 one-cycle JSON에는 로그 본문을 넣지 않고 단계별 trace, DB 검증, cleanup 상태만 포함합니다. 실제 호출 증명은 팀원이 `run_id`로 Backend stdout 로그를 조회해 완료합니다.

이 방식은 현재 저장소의 live runner와 Docker 운영 경로를 재사용하고 신규 DB·외부 플랫폼을 요구하지 않습니다.

## 아키텍처

```text
one-cycle 검증기
  │
  │ X-Validation-Run-Id: <run_id>
  ▼
FastAPI trace middleware
  ├─ run_id 허용 환경·형식 검증
  ├─ 서버 trace_id 생성
  ├─ ProviderCallContext 구성
  └─ X-Trace-Id 응답 헤더 추가
        │
        ▼
OCR / Guide / Chat Service
        │
        ▼
Provider Adapter
  ├─ provider.call.started
  ├─ 실제 CLOVA·OpenAI 호출
  └─ provider.call.succeeded 또는 provider.call.failed
        │
        ▼
FastAPI container stdout JSONL
        │
        ├─ Docker Desktop > fastapi > Logs
        └─ docker compose logs fastapi

one-cycle 검증기
  ├─ 단계별 X-Trace-Id 수집
  ├─ OCR·Guide·Chat DB 결과 검증
  ├─ cleanup 검증
  └─ 상관관계 JSON 출력
```

## 구성 요소

### `ProviderCallContext`

Provider 계층에 FastAPI `Request`를 직접 전달하지 않습니다. 요청 의존성 조립 단계에서 다음 내부 값 객체를 만들고 Provider 어댑터에 주입합니다.

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `trace_id` | 32자리 서버 생성 hexadecimal | 단일 HTTP 요청 식별자 |
| `validation_run_id` | UUID 또는 `null` | 승인된 one-cycle 검증 실행 식별자 |
| `environment` | 서버 환경 enum | 서버가 확인한 실행 환경, 이번 Live 검증은 `local`만 허용 |
| `validation_enabled` | boolean | Backend 검증 허용 설정 |

클라이언트가 주장한 실행 mode를 컨텍스트에 그대로 저장하지 않습니다. 환경과 검증 허용 여부는 Backend 설정에서 결정합니다.

### `ProviderCallDescriptor`

요청별 상관관계와 Provider 호출의 정적 의미를 분리합니다. 각 Provider 어댑터에는 다음 불변 descriptor를 생성 시점에 주입합니다.

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `provider` | `CLOVA_OCR` 또는 `OPENAI` | 실제 외부 Provider |
| `operation` | 승인된 operation enum | 호출 목적 |
| `prompt_version` | 문자열 또는 `null` | 해당 OpenAI Prompt Version, CLOVA는 `null` |

`requested_model`은 호출마다 `generate()`가 받는 동적 값이므로 descriptor에 중복 저장하지 않습니다. Guide·Chat·OCR 구조화 client가 자신이 어느 operation인지 추정하거나 Prompt 모듈을 역참조하지 않도록 dependency 조립 경계에서 명시합니다.

### `ProviderCallLogger`

Provider 이벤트 Schema의 유일한 직렬화 경계입니다.

- 고정 enum과 허용 필드만 입력받습니다.
- 한 이벤트를 한 개의 JSON object로 직렬화합니다.
- Provider 전용 logger는 formatter를 `%(message)s`로 고정합니다.
- 기존 `[timestamp] [level] [logger]` prefix를 Provider JSONL에 붙이지 않습니다.
- logger propagation을 끄고 중복 출력을 방지합니다.
- 한 logging record가 stdout 한 줄이 되도록 합니다.
- 직렬화 대상에 예외 객체, HTTP 객체, Pydantic 원본 객체를 받지 않습니다.

### Provider 어댑터

Provider 네트워크 경계를 실제로 아는 어댑터가 이벤트를 기록합니다.

- `ClovaOcrEngine`
- `OpenAIOcrStructureClient`
- Guide `OpenAIResponsesClient`
- Chat `OpenAIResponsesClient`

Service나 Repository는 실제 외부 호출 여부를 추정해 Provider 성공 로그를 만들지 않습니다.

Provider 어댑터는 일반 요청과 검증 요청 모두에 같은 안전한 이벤트 Schema를 사용합니다. 일반 요청은 `validation_run_id=null`이며, one-cycle 실제 호출 증빙은 승인된 `validation_run_id`가 있는 이벤트만 사용합니다.

최소 수집 원칙을 적용해 일반 요청에서는 `provider_request_id`와 `provider_response_id`를 항상 `null`로 둡니다. 이 두 외부 식별자는 승인된 Live 검증 요청에서만 기록합니다. Provider·operation·outcome·안전 오류 code·latency·요청 모델·실제 모델은 일반 운영 관측에도 필요한 허용 필드로 유지하되, 전체 호출 로그 범위는 Security·Privacy 승인 대상입니다.

### `NetworkOneCycleRunner`

기존 live runner를 확장합니다.

- 모든 요청에 동일 `X-Validation-Run-Id`를 유지합니다.
- 로그인 후 Authorization을 추가할 때 validation header를 덮어쓰지 않습니다.
- 모든 응답에서 `X-Trace-Id`를 수집합니다.
- Provider가 실행되는 OCR·Guide·Chat 요청 trace를 별도로 보존합니다.
- 오류 body의 `trace_id`와 응답 header가 모두 있으면 일치를 검증합니다.
- trace 누락 또는 불일치를 Live 검증 실패로 처리합니다.

## 요청 `run_id` 계약

### Header

```http
X-Validation-Run-Id: 61a10000-0000-4000-8000-000000000003
```

### 처리 규칙

| 조건 | 처리 |
| --- | --- |
| Header 없음 | 일반 요청으로 정상 처리 |
| 승인 환경이며 유효한 UUID | `validation_run_id`로 사용 |
| 승인 환경이며 형식 오류 | `400`과 기존 공통 code `HTTP_ERROR` |
| 미승인 환경이며 Header 존재 | `403`과 기존 공통 code `HTTP_ERROR` |

잘못된 validation header를 조용히 무시하지 않습니다. Live 실행이 성공한 것처럼 보이지만 상관관계가 사라지는 false positive를 막기 위해 fail-fast합니다.

공개 메시지는 입력값이나 환경 상세를 포함하지 않는 고정 문자열로 제한합니다.

- `400`: `Invalid validation run ID.`
- `403`: `Validation run is not allowed.`

### Backend 설정

현재 runner guard에서 사용하는 `RELEASE_VALIDATION_ALLOWED`를 Backend `Config`에도 명시적인 boolean 필드로 추가합니다.

- 기본값은 `false`입니다.
- `local`에서만 `true`를 허용합니다.
- `staging`과 `production` 등 local 이외 환경에서 `true`이면 애플리케이션 기동을 거부합니다.
- runner와 Backend는 서로 다른 process이므로 각각의 환경에 명시적으로 주입합니다.
- runner의 값은 Backend 설정을 증명하지 않고, Backend의 값도 runner guard를 대체하지 않습니다.
- local Compose와 검증 실행 문서에 어느 process에 값이 주입되는지 구분해 기록합니다.

이 설정이 없거나 `false`이면 일반 요청은 그대로 처리하지만 `X-Validation-Run-Id`가 있는 요청은 `403 HTTP_ERROR`로 거부합니다. 이번 범위에서 신규 공개 오류 code를 만들지 않습니다. Header별 고정된 안전 메시지와 HTTP status는 Current 계약에 기록하고 runner는 status·trace 일치를 검증합니다.

### 보안 경계

- `run_id`는 인증·인가 수단이 아닙니다.
- 사용자·Profile·Document·Job 소유권 판정에 사용하지 않습니다.
- DB 의료정보 조회 조건으로 사용하지 않습니다.
- Provider metadata로 전송하지 않습니다.
- UUID 외 문자열을 로그에 넣지 않습니다.
- 서로 다른 실행의 악의적 혼합을 방지하는 감사급 보안 토큰으로 간주하지 않습니다.
- Live 증빙은 고엔트로피 UUID, 실행 시간, commit 또는 image digest, 서버 환경을 함께 확인합니다.

## 응답 `trace_id` 계약

Backend는 성공·실패 응답에 다음 Header를 포함합니다.

```http
X-Trace-Id: <server-generated-trace-id>
```

규칙은 다음과 같습니다.

- Backend가 요청마다 128-bit 무작위 값을 생성합니다.
- 외부에서 전달된 trace 값을 서버 trace로 신뢰하지 않습니다.
- 오류 응답 body의 기존 `trace_id`와 같은 값을 사용합니다.
- body와 header 값이 다르면 계약 위반입니다.
- CORS를 통해 읽을 수 있도록 `X-Trace-Id`를 exposed header에 추가합니다.
- Nginx가 요청 `X-Validation-Run-Id`와 응답 `X-Trace-Id`를 제거하거나 덮어쓰지 않아야 합니다.
- API와 release validation 문서에 Header 계약을 기록합니다.

## Provider 호출 이벤트 계약

### 수명주기

```text
provider.call.started
        │
        ├─ 정상 완료 ───────────────▶ provider.call.succeeded
        │
        ├─ 호출·응답·검증 실패 ───▶ provider.call.failed
        │
        └─ 프로세스 강제 종료 ─────▶ terminal 없음 → INCOMPLETE
```

- 실제 네트워크 호출 직전에 `started`를 기록합니다.
- HTTP 응답 수신과 Provider client 내부의 Provider Schema 파싱을 모두 통과한 뒤 `succeeded`를 기록합니다.
- 호출 또는 응답 검증 실패 시 `failed`를 기록합니다.
- Provider 호출 전 입력 검증에서 실패하면 Provider 이벤트를 만들지 않습니다.
- 동일 `provider_call_id`에 terminal 이벤트는 최대 1건입니다.
- 시작 이벤트만 존재하면 `INCOMPLETE`입니다.

Provider terminal은 외부 호출 경계만 나타냅니다. Generator·Structurer가 client 반환 이후 수행하는 Grounding, 의료 안전, 정규화, renderer 검증은 Provider terminal을 변경하지 않습니다. Provider가 유효한 응답을 반환했지만 후속 애플리케이션 검증이 실패하면 Provider event는 `succeeded`, DB·one-cycle 결과는 `FAILED`가 될 수 있으며 최종 증빙은 DB 검증 실패로 거부합니다.

### 공통 Schema

```json
{
  "schema_version": "provider-call-log-v1",
  "event": "provider.call.succeeded",
  "occurred_at": "2026-09-01T12:00:00.123Z",
  "environment": "local",
  "validation_run_id": "61a10000-0000-4000-8000-000000000003",
  "trace_id": "server-generated-trace-id",
  "provider_call_id": "server-generated-call-id",
  "provider": "OPENAI",
  "operation": "GUIDE_GENERATION",
  "requested_model": "gpt-4o-mini",
  "model_name": "gpt-4o-mini-2024-07-18",
  "prompt_version": "guide-prompt-v3",
  "provider_request_id": null,
  "provider_response_id": "resp_...",
  "provider_response_received": true,
  "http_status": null,
  "latency_ms": 842,
  "outcome": "SUCCESS",
  "failure_phase": null,
  "error_code": null
}
```

### 필드 규칙

| 필드 | 규칙 |
| --- | --- |
| `schema_version` | `provider-call-log-v1` 고정 |
| `event` | `provider.call.started`, `provider.call.succeeded`, `provider.call.failed` |
| `occurred_at` | UTC ISO 8601, 로그 표시용 wall clock |
| `environment` | Backend 설정에서 확인한 값 |
| `validation_run_id` | 승인된 검증 요청이면 UUID, 일반 요청이면 `null` |
| `trace_id` | Backend가 생성한 요청 trace |
| `provider_call_id` | 호출 시작·terminal 연결용 UUID |
| `provider` | `CLOVA_OCR`, `OPENAI` |
| `operation` | 승인된 operation enum |
| `requested_model` | OpenAI 설정 모델, CLOVA는 `null` |
| `model_name` | 성공 응답에서 확인한 실제 OpenAI 모델, 그 외 `null` |
| `prompt_version` | OpenAI operation의 애플리케이션 Prompt Version |
| `provider_request_id` | 승인된 Live validation에서 Provider가 안전하게 제공한 경우만 기록, 일반 요청은 `null` |
| `provider_response_id` | 승인된 Live validation에서 OpenAI Response `id` 등 안전하게 확인된 값, 일반 요청은 `null` |
| `provider_response_received` | Provider 응답 수신 여부 |
| `http_status` | 실제 객체에서 확인한 경우만 기록 |
| `latency_ms` | monotonic clock 기준, terminal에서만 정수 |
| `outcome` | `STARTED`, `SUCCESS`, `FAILED` |
| `failure_phase` | 실패 시 승인 enum, 그 외 `null` |
| `error_code` | 실패 시 안전한 allowlist code, 그 외 `null` |

`occurred_at`은 사람의 로그 조회용이고 latency 계산에는 사용하지 않습니다. latency는 시스템 wall clock 변경의 영향을 받지 않도록 monotonic clock으로 계산합니다.

### Operation enum

- `PRESCRIPTION_RECOGNITION`
- `OCR_STRUCTURING`
- `GUIDE_GENERATION`
- `CHAT_GENERATION`

임의 문자열을 허용하지 않습니다. operation 추가 시 Schema 호환성과 문서·테스트를 함께 검토합니다.

### 실패 단계 enum

- `TRANSPORT_TIMEOUT`
- `TRANSPORT_CONNECTION`
- `HTTP_STATUS`
- `RESPONSE_VALIDATION`
- `PROVIDER_POLICY`
- `APPLICATION_DEADLINE`
- `UNKNOWN_INTERNAL`

`UNKNOWN_INTERNAL`은 예상하지 못한 예외를 안전하게 분류하기 위한 값입니다. 예외 클래스명과 메시지를 로그에 넣지 않습니다.

### 내부 안전 오류 code

- `PROVIDER_TIMEOUT`
- `PROVIDER_CONNECTION_FAILED`
- `PROVIDER_RATE_LIMITED`
- `PROVIDER_UNAVAILABLE`
- `PROVIDER_REQUEST_REJECTED`
- `PROVIDER_RESPONSE_INVALID`
- `PROVIDER_REFUSAL`
- `PROVIDER_SAFETY_FILTERED`
- `PROVIDER_CALL_ABORTED`
- `PROVIDER_INTERNAL_FAILURE`

공개 API 오류 code와 Provider 로그 code는 별도 계약입니다. Provider 원본 오류 문자열을 그대로 저장하지 않습니다.

## Provider별 기록 규칙

### CLOVA OCR

- `PRESCRIPTION_RECOGNITION` span은 실제 `_request()` 직전 시작하고 `_parse_response()`가 성공한 직후 종료합니다.
- 이후 규칙 기반 구조화 또는 `LlmPrescriptionStructurer.structure()`는 CLOVA span에 포함하지 않습니다.
- OCR 구조화 OpenAI가 활성화되면 같은 HTTP `trace_id` 아래 별도 `OCR_STRUCTURING` span을 만듭니다.
- 애플리케이션이 생성한 CLOVA V2 `requestId`를 `provider_request_id`로 기록할 수 있습니다.
- 실제 `httpx.Response.status_code`만 `http_status`에 기록합니다.
- timeout·connection failure에서는 `http_status=null`입니다.
- Secret, Invoke URL query, 파일명, 파일 경로, 이미지 byte, OCR 원문, Provider body는 기록하지 않습니다.
- 응답 수신 후 OCR Schema 검증 실패는 `provider_response_received=true`, `failure_phase=RESPONSE_VALIDATION`입니다.

### OpenAI OCR 구조화·Guide·Chat

- span은 OpenAI SDK 호출 직전 시작하고 client 내부의 완료 상태·refusal·Provider 응답 Schema 파싱이 끝난 시점에 terminal 처리합니다.
- Generator·Structurer의 후속 Grounding·도메인 안전 검증은 OpenAI Provider span 밖입니다.
- Provider refusal과 content filter는 `failure_phase=PROVIDER_POLICY`로 기록합니다.
- 요청 시 설정 모델을 `requested_model`에 기록합니다.
- 성공 시 Response 객체의 `id`와 `model`을 기록합니다.
- `APIStatusError`에서 안전하게 확인한 `request_id`와 `status_code`만 기록합니다.
- timeout·connection failure에서 확인할 수 없는 값은 `null`입니다.
- SDK 고수준 성공 응답이 실제 HTTP status를 제공하지 않으면 `200`으로 추정하지 않습니다.
- input, instructions, output, token 본문, 전체 Prompt, SDK 예외 메시지를 기록하지 않습니다.
- `store=False` 설정은 기존 계약대로 유지하지만 로그 안전성을 대체하지 않습니다.

OpenAI 공식 Responses 객체의 `id`와 `model`은 실제 응답 식별과 모델 확인에 사용합니다. 이 값은 Provider payload 전체를 보관하지 않고도 호출 결과를 추적할 수 있는 최소 메타데이터입니다.

## Cancellation과 예외 처리

- Provider timeout·application deadline·task cancellation에서 가능한 경우 `provider.call.failed`를 기록합니다.
- `asyncio.CancelledError`를 일반 오류로 삼켜서는 안 됩니다.
- cancellation terminal 이벤트를 best effort로 기록한 뒤 반드시 cancellation을 다시 전파합니다.
- 로그 기록을 기다리느라 요청 deadline을 의미 있게 초과하지 않도록 동기 stdout logging만 사용합니다.
- 프로세스 kill처럼 terminal 기록 자체가 불가능한 경우 started-only `INCOMPLETE`로 판정합니다.
- Provider adapter의 기존 provider-neutral 예외 매핑을 변경하지 않습니다.

## 로그 기록 실패

로그는 관측 수단이며 의료 기능의 성공·실패 결정자가 아닙니다.

- JSON 직렬화 또는 logger 출력 실패가 Provider 결과를 바꾸지 않습니다.
- 기록 실패 시 의료정보 없는 고정 경고 `provider_log_emit_failed=true`만 남깁니다.
- fallback 경고도 직렬화 실패를 재귀적으로 일으키지 않도록 상수 메시지를 사용합니다.
- 로그 기록 실패가 발생한 Live 실행은 Provider 자체 성공 여부와 관계없이 증빙 미완료입니다.
- Provider 호출 성공을 복구하기 위해 로그 때문에 자동 재호출하지 않습니다.

## one-cycle 결과 계약

### Provider trace

```json
{
  "provider_traces": {
    "prescription_recognition": {
      "status": "EXPECTED",
      "trace_id": "trace-ocr"
    },
    "ocr_structuring": {
      "status": "EXPECTED",
      "trace_id": "trace-ocr"
    },
    "guide_generation": {
      "status": "EXPECTED",
      "trace_id": "trace-guide"
    },
    "chat_generation": {
      "status": "EXPECTED",
      "trace_id": "trace-chat"
    }
  }
}
```

OCR 구조화 OpenAI가 비활성화된 경우 다음처럼 표현합니다.

```json
{
  "ocr_structuring": {
    "status": "SKIPPED",
    "reason": "OCR_STRUCTURE_LLM_DISABLED",
    "trace_id": null
  }
}
```

활성화되지 않은 Provider 호출에 실제 호출 trace가 있다고 주장하지 않습니다.

### 최종 결과

```json
{
  "run_id": "61a10000-0000-4000-8000-000000000003",
  "mode": "local-live-full",
  "execution_mode": "LIVE",
  "transport": "network",
  "provider_traces": {},
  "database_verification": "PASS",
  "cleanup": "PASS",
  "provider_log_verification": "MANUAL_REQUIRED",
  "execution": "PASS"
}
```

`execution=PASS`는 API·DB·cleanup 검증 성공을 의미합니다. Backend 로그를 사람이 확인했다는 의미가 아닙니다. 애플리케이션이 조회하지 않은 로그 상태를 자동으로 `PASS`라고 출력하지 않습니다.

최종 증빙 완료 판정은 별도 수동 검토 record에서 수행합니다.

```json
{
  "schema_version": "provider-log-review-v1",
  "run_id": "61a10000-0000-4000-8000-000000000003",
  "reviewed_at": "2026-09-01T12:10:00Z",
  "reviewer": "designated-reviewer",
  "required_operations": {
    "PRESCRIPTION_RECOGNITION": "PASS",
    "OCR_STRUCTURING": "PASS",
    "GUIDE_GENERATION": "PASS",
    "CHAT_GENERATION": "PASS"
  },
  "trace_match": "PASS",
  "sensitive_data_check": "PASS",
  "result": "PASS"
}
```

Issue·멘토링 증빙의 전체 성공은 `one-cycle execution=PASS`, `database_verification=PASS`, `cleanup=PASS`, `provider-log-review result=PASS`를 모두 만족해야 합니다. 수동 검토자가 기록되지 않으면 증빙은 미완료입니다.

## 실제 Live 실행 판정

최종 결과의 `execution_mode=LIVE`는 클라이언트 Header 하나로 결정하지 않습니다. 다음 기존 guard와 검증을 모두 만족해야 합니다.

- runner mode가 `local-live-full`
- runner와 FastAPI 사이 실제 TCP transport
- runner process에 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY` 부재
- Backend process에 승인된 Provider Secret 주입
- dependency override와 fake Provider 사용 금지
- DB 모델명에 `fake`, `sentinel`, `test-model` 부재
- 현재 commit SHA 또는 image repository digest 기록
- 합성 Fixture identity와 DB identity 일치

OCR 구조화 기대값은 runner 환경변수 하나만으로 결정하지 않습니다.

- runner가 검증한 `OCR_STRUCTURE_LLM_ENABLED`와 Backend 배포 설정이 일치해야 합니다.
- 활성 경로는 OCR DB의 `model_version`·`prompt_version`이 non-null이고 `OCR_STRUCTURING` 로그가 존재해야 합니다.
- 비활성 경로는 두 DB 필드가 `null`이고 `OCR_STRUCTURING` 로그가 없어야 합니다.
- 설정·DB·Provider 로그 중 하나라도 다르면 DB 검증 또는 수동 Provider 로그 검토를 실패 처리합니다.

Provider 구조화 로그는 위 guard 결과와 함께 실제 호출 증빙을 구성합니다. 로그만으로 감사급 또는 암호학적 호출 증명을 제공한다고 주장하지 않습니다.

## 팀원 로그 확인 절차

### Docker Desktop

1. one-cycle Live 검증을 실행합니다.
2. 최종 JSON에서 `run_id`를 복사합니다.
3. Docker Desktop의 Containers에서 `fastapi` 컨테이너를 선택합니다.
4. Logs 화면에서 `run_id`를 검색합니다.
5. 필수 operation의 terminal 이벤트를 확인합니다.
6. 최종 JSON의 trace와 로그의 trace가 일치하는지 확인합니다.

Docker Desktop의 통합 Logs 화면을 사용할 수 있는 버전에서는 `fastapi` container filter와 정확한 `run_id` 검색을 함께 사용합니다.

### Docker CLI 대체 경로

```bash
docker compose logs --no-color --no-log-prefix --since 10m fastapi \
  | rg '"validation_run_id":"61a10000-0000-4000-8000-000000000003"'
```

실시간 조회:

```bash
docker compose logs --no-color --no-log-prefix -f fastapi \
  | rg '"validation_run_id":"61a10000-0000-4000-8000-000000000003"'
```

Docker Desktop 검색은 빠른 육안 확인용입니다. Desktop Export는 service prefix나 UI 변환이 포함될 수 있으므로 정본 JSONL Artifact로 사용하지 않습니다. 정본 발췌는 컨테이너 stdout 원문에서 생성합니다.

```bash
docker logs --since 10m fastapi 2>&1 \
  | rg '"validation_run_id":"61a10000-0000-4000-8000-000000000003"' \
  > /private/tmp/provider-call-log-61a10000-0000-4000-8000-000000000003.jsonl
chmod 600 /private/tmp/provider-call-log-61a10000-0000-4000-8000-000000000003.jsonl
```

발췌 후 각 줄이 독립 JSON 객체인지 파싱하고, `schema_version=provider-call-log-v1`과 금지 필드 부재를 검사합니다. Compose service name이 다른 환경에서는 먼저 실제 Backend 컨테이너 이름을 확인한 뒤 명시적으로 치환합니다.

## 수동 Provider 로그 판정

| Operation | 필수 terminal |
| --- | --- |
| `PRESCRIPTION_RECOGNITION` | `provider.call.succeeded` 1건 |
| `OCR_STRUCTURING` | 기능 활성 시 `provider.call.succeeded` 1건, 비활성 시 로그 없음 |
| `GUIDE_GENERATION` | `provider.call.succeeded` 1건 |
| `CHAT_GENERATION` | `provider.call.succeeded` 1건 |

다음 조건은 증빙 실패입니다.

- `started`만 있고 terminal 이벤트가 없음
- 같은 `provider_call_id`에 terminal 이벤트가 2건 이상
- 필수 operation 누락
- 필수 operation의 terminal이 `failed`
- 로그 `trace_id`와 runner 결과가 다름
- Provider 로그는 성공했지만 DB 상태 검증이 실패
- cleanup이 `PASS`가 아님
- 로그에 금지 정보가 포함됨
- Mock·dependency override 실행을 Live로 표시함

## 증빙 Artifact

멘토링 또는 릴리스 검토용 증빙은 다음 세 부분으로 구성합니다.

```text
one-cycle-result.json
provider-call-log-<run_id>.jsonl
provider-log-review-<run_id>.json
```

- `one-cycle-result.json`은 runner의 민감정보 없는 최종 결과입니다.
- `provider-call-log-<run_id>.jsonl`은 `docker logs`의 prefix 없는 stdout 원문에서 해당 `run_id`만 필터링한 발췌입니다.
- `provider-log-review-<run_id>.json`은 지정 검토자가 operation·trace·민감정보를 판정한 수동 검토 기록입니다.
- 전체 증빙 성공은 runner 결과·DB 검증·cleanup과 수동 Provider 로그 검토가 모두 `PASS`일 때만 성립합니다.
- 애플리케이션이 별도 로그 파일을 자동 생성하지 않습니다.
- 전체 컨테이너 로그를 첨부하지 않습니다.
- 증빙 발췌에 금지 필드가 없는지 sentinel 검사 후 사용합니다.
- 증빙 파일은 저장소에 commit하지 않습니다.
- 접근 제한된 Issue·검증 저장 위치에만 첨부하고 검토 완료 후 팀 보존 정책에 따라 삭제합니다.

## 로그 보존과 접근

이번 작업은 Docker stdout을 사용하므로 장기 보존을 보장하지 않습니다.

- Live 실행 직후 `run_id`로 조회합니다.
- Docker restart, logging driver rotation, Desktop clear 이후 조회 가능성을 보장하지 않습니다.
- Provider response ID와 trace는 접근 제한된 Backend 로그에서만 조회합니다.
- 장기 중앙 보존, log shipping, retention 자동화는 별도 Infrastructure Issue입니다.
- 운영 로그 접근권한과 보존기간은 기존 Security·Privacy 운영 정책을 따릅니다.

## 개인정보·보안 금지 필드

일반 로그와 증빙 발췌에 다음 값을 기록하지 않습니다.

- OpenAI API Key
- CLOVA Secret
- Authorization 및 Provider 인증 Header
- 처방전 이미지와 byte
- 파일 경로와 object key
- OCR 원문과 token text
- 약명·용량 등 의료정보
- 사용자 질문 및 AI 답변 본문
- 사용자·Profile·Document·Prescription 식별자
- Provider 요청·응답 payload
- instructions와 Prompt 본문
- SDK 예외 원문과 객체 `repr`

`run_id`, `trace_id`, `provider_call_id`, Provider request·response ID는 연결 가능한 운영 식별자이므로 일반 공개 대상이 아닙니다. 접근 제한 로그와 승인된 증빙 범위에서만 사용합니다.

## 데이터베이스 경계

- 신규 table과 column을 만들지 않습니다.
- Provider log ID를 OCR·Guide·Chat table에 저장하지 않습니다.
- 기존 OCR `engine_name`, `model_version`, `prompt_version` 의미를 유지합니다.
- 기존 Guide·Chat `model_name`, `prompt_version`, 상태와 오류 필드를 그대로 검증합니다.
- Provider 로그와 DB 결과의 연결은 `run_id`·`trace_id`와 runner가 수집한 기존 리소스 ID를 통해 검증 시점에 수행합니다.
- `run_id`를 의료 row에 영속화하지 않습니다.

## 예상 변경 파일

구현 시 다음 영역이 영향을 받습니다. 이 목록은 구현 계획이 아니라 설계상 영향 범위입니다.

- `backend/app/main.py`
  - validation run ID 처리
  - 성공·실패 `X-Trace-Id`
  - CORS exposed header
- `backend/app/core/`
  - `ProviderCallContext`
  - Provider JSONL logger
  - 고정 enum과 안전 오류 매핑
- `backend/app/core/config.py`
  - typed `RELEASE_VALIDATION_ALLOWED=false`
  - Production 활성화 기동 거부
- `backend/app/dependencies/services.py`
  - 요청 context 조립과 Provider 주입
- `backend/app/services/clova_ocr_engine.py`
- `backend/app/services/ocr_ai/client.py`
- `backend/app/services/guide_ai/client.py`
- `backend/app/services/chat_ai/client.py`
- `backend/app/release_validation/ai_one_cycle_smoke.py`
- `docker-compose.yml`
  - local Backend 검증 설정 전달
- 관련 `backend/app/tests/`
- `docs/api.md`
- `docs/validation/ai-one-cycle-release.md`
- `docs/contracts/current/live-provider-call-evidence.md`
  - `X-Validation-Run-Id`, `X-Trace-Id`, Provider 증빙 Schema와 수동 판정 계약 신설
- `docs/contracts/current/backend-error-response.md`
  - 기존 `HTTP_ERROR`를 사용하는 validation Header 400·403과 Header/body trace 일치 반영
- `docs/contracts/README.md`
  - 새 current 계약 색인 추가

공유 Header 계약 변경이므로 Backend/API, OCR, AI/RAG, Evaluation 담당 리뷰를 지정합니다. Security·Privacy 담당자는 금지 필드, 로그 접근과 증빙 보존 경계를 검토합니다.

## 테스트 전략

### Request context와 Header

- Header 없음은 일반 요청
- 승인 환경의 유효 UUID 수용
- 승인 환경의 형식 오류 거부
- 미승인 환경의 Header 거부
- `run_id`가 인증·소유권에 영향을 주지 않음
- 성공 응답 `X-Trace-Id`
- 기존 오류 body trace와 Header 일치
- 미등록 경로 404의 `X-Trace-Id`
- 허용되지 않은 method 405의 `X-Trace-Id`
- 처리되지 않은 예외 500의 `X-Trace-Id`
- 가장 바깥 ASGI 경계에서 성공·기본 오류·예외 응답에 Header가 일관되게 추가됨
- Nginx Header pass-through
- CORS exposed header

### Logger 단위 테스트

- 한 record가 prefix 없는 JSON 한 줄
- `provider-call-log-v1` Schema 필수 필드
- enum 외 값 거부
- started·succeeded·failed 조합
- UUID·trace 형식
- UTC timestamp
- monotonic latency
- `null`과 조건부 필드 규칙
- `ProviderCallDescriptor`의 provider·operation·prompt_version 정적 매핑
- 실제 호출 인자의 requested_model과 응답 model_name 동적 기록
- 일반 요청에서는 Provider request·response ID가 `null`이고 승인된 Live validation에서만 기록됨
- 직렬화 실패가 Provider 결과를 변경하지 않음
- logger 중복 handler·propagation으로 중복 출력되지 않음

### CLOVA 어댑터

- 2xx 성공
- timeout
- connection failure
- 4xx
- 429
- 5xx
- 응답 JSON·Schema 검증 실패
- 실제 확인한 status만 기록
- request ID 보존
- Secret·URL·파일·OCR 본문 비노출

### OpenAI 어댑터

OCR 구조화, Guide, Chat 각각 다음을 검증합니다.

- 성공
- timeout
- connection failure
- rate limit
- API status error
- 응답 검증 실패
- refusal와 safety filter
- actual response ID와 model
- 확인할 수 없는 HTTP status의 `null`
- input·instructions·output·예외 원문 비노출

### Cancellation

- task cancellation에서 cancellation 재전파
- 가능한 경우 `PROVIDER_CALL_ABORTED` terminal
- cancellation을 정상 결과로 변환하지 않음
- terminal 기록 실패가 cancellation을 막지 않음

### Runner 통합

- 로그인 이후에도 validation Header 유지
- OCR·Guide·Chat response trace 수집
- OCR 구조화 활성·비활성 분기
- Header trace 누락 실패
- 오류 body·Header trace 불일치 실패
- Mock 실행이 Live 증빙으로 분류되지 않음
- DB 검증과 cleanup 기존 결과 유지
- 실제 TCP transport guard 회귀 없음
- final JSON에 Provider payload·의료본문 없음

### 민감정보 sentinel

합성 sentinel을 Secret, 질문, OCR 원문, 파일 경로, Prompt 위치에 주입하고 caplog·stdout·final JSON 전체에서 부재를 확인합니다.

실제 Secret이나 실제 환자정보를 fixture에 넣지 않습니다.

### Live 검증

- 승인된 비식별 합성 Fixture
- `local-live-full` 실제 CLOVA·OpenAI 호출
- Docker Desktop 또는 CLI에서 `run_id` 조회
- 필수 operation terminal 확인
- runner trace와 로그 trace 일치
- OCR·Guide·Chat DB 상태 확인
- cleanup `PASS`
- 로그 발췌 sentinel 검사
- prefix 없는 JSONL 전체 줄 파싱과 Schema 검증
- 수동 검토 기록 Schema와 필수 operation 판정
- clean worktree와 commit SHA 기록

## Rollout

1. Header·context·logger 계약을 단위 테스트로 고정합니다.
2. 각 Provider 어댑터를 독립적으로 계측합니다.
3. runner에 validation Header와 trace 수집을 연결합니다.
4. Mock 통합 테스트에서 Schema·금지 필드를 검증합니다.
5. 로컬 Docker에서 비식별 Live one-cycle을 실행합니다.
6. Docker Desktop과 CLI에서 동일 `run_id` 결과를 확인합니다.

기존 사용자 요청은 validation Header가 없으므로 기존 동작을 유지합니다. Header·logger 문제가 의료 결과나 Provider retry 동작을 변경하지 않아야 합니다.

## 완료 조건

- [ ] 승인된 `local-live-full` 실행이 실제 TCP 경로로 성공합니다.
- [ ] CLOVA `PRESCRIPTION_RECOGNITION` terminal 로그가 조회됩니다.
- [ ] 활성화된 경우 OpenAI `OCR_STRUCTURING` terminal 로그가 조회됩니다.
- [ ] OpenAI `GUIDE_GENERATION` terminal 로그가 조회됩니다.
- [ ] OpenAI `CHAT_GENERATION` terminal 로그가 조회됩니다.
- [ ] `run_id`로 한 실행의 Provider 로그를 검색할 수 있습니다.
- [ ] runner trace와 Provider 로그 trace가 일치합니다.
- [ ] started와 terminal이 `provider_call_id`로 연결됩니다.
- [ ] Docker Desktop과 CLI에서 동일 로그를 확인할 수 있습니다.
- [ ] 정본 JSONL은 `docker logs` 원문에서 추출되고 모든 줄이 독립 JSON으로 파싱됩니다.
- [ ] OCR·Guide·Chat DB 검증이 통과합니다.
- [ ] cleanup이 `PASS`입니다.
- [ ] `one-cycle-result.json`, Provider JSONL, 수동 검토 기록이 같은 `run_id`로 연결됩니다.
- [ ] runner·DB·cleanup·수동 Provider 검토가 모두 `PASS`인 경우에만 전체 증빙을 성공으로 판정합니다.
- [ ] Mock 실행은 Live 증빙으로 인정되지 않습니다.
- [ ] API Key·의료정보·Provider payload 비노출 테스트가 통과합니다.
- [ ] 신규 DB Schema와 외부 로그 플랫폼이 추가되지 않습니다.
- [ ] current 공유 계약, 계약 색인, Backend 오류 계약, API·release validation 문서가 함께 갱신됩니다.
- [ ] 관련 단위·통합·회귀 테스트가 통과합니다.
- [ ] Ruff와 mypy가 통과합니다.
- [ ] Backend/API, OCR, AI/RAG, Evaluation 지정 리뷰가 완료됩니다.
- [ ] Security·Privacy 로그·증빙 경계 검토가 완료됩니다.

## 구현 착수 Gate

이 문서는 구현 전 설계만 확정합니다. 구현은 다음 조건 이후 별도 계획에 따라 착수합니다.

- Issue #152에 구현 담당자와 영역별 담당 리뷰어 명시
- Security 기술 검토자와 Privacy 정책·증빙 보존 승인자를 별도로 명시
- `X-Validation-Run-Id`, `X-Trace-Id` 공유 Header 계약 승인
- Security·Privacy 로그 허용 필드와 증빙 보존 경계 승인
- 실제 Provider 호출이 허용된 local 검증 환경 확인
- 설계 문서 사용자 검토 완료
