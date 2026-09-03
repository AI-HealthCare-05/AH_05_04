# Provider observability context 및 실패 증적 정책 설계

## 1. 목적과 범위

이 문서는 Issue #211의 확정 설계입니다. `local-live-full` Provider 호출 증적에서 컨텍스트 누락을 조용히 무시하지 않고, 실패 Artifact가 실제로 수행한 검증만 표현하도록 합니다.

다음은 변경하지 않습니다.

- `provider-call-log-v1`의 20개 필드와 허용 enum
- 공개 API status, error code, `details` 의미
- Provider payload 처리, timeout, retry, 생성 동작
- `WorkerMessage.trace_id` 형식
- staging·production Live 검증과 배포 설정
- 기존 #152 Artifact를 얻기 위한 추가 Provider Live 호출

## 2. Provider observability 상태 모델

`ProviderCallObserver`는 다음 두 상태만 허용합니다.

| 상태 | `observability_disabled` | context | descriptor | 결과 |
| --- | --- | --- | --- | --- |
| active | `false` | 필수 | 필수 | Provider span 기록 |
| disabled | `true` | 없음 | 없음 | span을 만들지 않음 |

context와 descriptor가 하나라도 없거나 disabled 상태에 둘 중 하나를 제공하면 생성 시 즉시 실패합니다. 따라서 observability가 활성인 경로에서 조립 오류가 로그 0건으로 숨지 않습니다.

HTTP Provider 어댑터는 항상 active입니다. FastAPI DI는 요청 middleware가 만든 `ProviderCallContext`와 operation별 고정 `ProviderCallDescriptor`를 반드시 전달하며 context 없음 fallback을 제공하지 않습니다.

현재 Worker에는 Provider 어댑터 조립 경로가 없고 #141도 OCR·Guide·Chat Handler 비즈니스 로직을 제외합니다. `ProviderCallContext`·`ProviderCallDescriptor`를 현재 위치에서 import하면 `app.core.__init__`의 import-time `get_config()`가 실행되어 Worker에 Backend DB 설정을 요구하므로 그대로 재사용하지 않습니다. [Issue #231](https://github.com/AI-HealthCare-05/AH_05_04/issues/231)에서 Backend 설정과 독립된 공용 타입 경계와 Worker environment 출처를 구현하기 전에는 Worker Provider adapter를 조립하지 않습니다. #231 이후 비동기 Worker는 검증된 메시지의 `trace_id`, Worker 설정의 environment, `validation_run_id=null`, `validation_enabled=false`로 context를 만들고 operation별 descriptor를 함께 전달합니다.

테스트를 제외한 runtime explicit disabled allowlist는 local release evidence 범위 밖의 `backend.app.evaluation.chat_history_runner.execute` 하나입니다. 새 runtime opt-out은 current contract와 관련 회귀 테스트를 함께 갱신하고 Backend/API·Security 및 영향 도메인 리뷰를 받아야 합니다. HTTP dependency wiring과 향후 Worker Provider wiring은 opt-out할 수 없습니다.

## 3. runner API 실패 discriminator

runner는 공개 오류 body를 변경하지 않고 기존 `details.reason`에서 다음 값만 `failure_evidence.api_reason`으로 복사합니다.

- `DEADLINE_EXCEEDED`
- `PROVIDER_TIMEOUT`

문자열이 아니거나 allowlist 밖이면 필드 자체를 생략합니다. `details`의 다른 key, 값, 객체 표현은 Artifact에 복사하지 않습니다. 이 값은 동일한 공개 `OCR_PROVIDER_TIMEOUT` code에서 애플리케이션 전체 예산 소진과 Provider transport timeout을 구분하기 위한 runner 전용 증적입니다.

## 4. local-live-full 실패 Artifact 상태

Backend에 첫 요청을 보내기 전 guard·scenario 실패에는 Live 증적 필드를 추가하지 않습니다. local Live 실행 경계에 진입한 뒤에는 성공과 실패 모두 다음 상태를 기록합니다.

| 필드 | 값 | 의미 |
| --- | --- | --- |
| `execution_mode` | `LIVE` | `local-live-full` 실행 경계에 진입함 |
| `database_verification` | `NOT_RUN` | DB 검증 단계에 도달하지 않음 |
| `database_verification` | `FAIL` | DB 검증을 수행했으나 실패함 |
| `database_verification` | `PASS` | DB 검증을 수행하고 통과함 |
| `provider_log_verification` | `MANUAL_REQUIRED` | 하나 이상의 Provider trace를 확보해 수동 JSONL 검토가 필요함 |
| `provider_log_verification` | `UNVERIFIED` | 수동 검토에 사용할 Provider trace가 없음 |

`provider_log_verification`은 자동으로 `PASS`가 되지 않습니다. API 실패는 DB 상태 `NOT_RUN`, DB 검증 실패는 `FAIL`, DB 검증 뒤 발생한 safety 실패는 `PASS`를 유지합니다. cleanup `PENDING`을 포함한 모든 terminal 결과에도 현재까지 관측한 상태를 보존합니다.

## 5. 기존 #152 Artifact의 수동 완료 조건

추가 Live 호출 없이 다음 기존 Artifact만 사용합니다.

- run ID: `2d8d3356-d019-430f-a31b-34d5c2afaf71`
- artifact commit: `9c41189c47d8cddd07f26b3c699f0ac74b99aa2a`
- 실행 당시 PR head: `956e3f6ff73a056359c3d9ae5d4987d7ca451166`
- one-cycle result SHA-256: `43234ff1185a6de41f2885c022e486cc28ae757784ba8bb64e090b1e356f1d56`
- Provider JSONL SHA-256: `6fcd4bf43c9a5e28eff1b7813f9a89409f12aabba6ec85f5fc19daaa2f22ebf2`

완료에는 다음 외부 작업이 필요합니다.

1. 저장소 밖의 승인된 접근 제한 보관 위치를 지정합니다.
2. `/private/tmp`의 두 Artifact를 그 위치로 이동하고 SHA-256 무결성을 다시 확인합니다.
3. 지정된 사람 검토자가 JSONL을 `provider-log-review-v1` 절차로 검토하고 별도 review Artifact를 작성합니다.

도구나 자동 테스트는 사람 검토자의 이름, 판정, 서명을 대신 만들지 않습니다. 승인 위치와 지정 검토자가 확정되기 전까지 수동 증적은 미완료입니다.
