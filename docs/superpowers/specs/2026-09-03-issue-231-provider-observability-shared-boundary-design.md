# Issue #231 Provider Observability Shared Boundary Design

> **상태:** Approved target / Not implemented
> **Issue:** [#231](https://github.com/AI-HealthCare-05/AH_05_04/issues/231)
> **구현 담당자:** 정현우 `@ceohwj`
> **Backend/API·Security 리뷰:** 송은영 `@phina-io`
> **Worker/OCR 리뷰:** 김지혜 `@Jye-rookie`

## 1. 목적

Backend와 AI Worker가 Provider 호출의 context·descriptor·enum을 하나의 import-neutral 계약으로 사용하게 합니다. 공용 계약을 import하거나 Worker context를 생성하는 과정에서는 Backend `Config`, DB 설정, FastAPI, Redis, logger를 초기화하지 않습니다.

이 설계는 #232 OCR Worker Handler가 기존 Provider observability 규칙과 동일한 trace·operation 의미를 사용하기 위한 선행 경계입니다. 실제 CLOVA adapter와 OCR Handler 조립은 #232가 소유합니다.

## 2. 현재 문제

Provider observability 데이터 타입은 `backend/app/core/provider_observability.py`에 있고 `DeploymentEnvironment`에 해당하는 `Env`는 `backend/app/core/config.py`에 있습니다. Worker가 현재 타입을 `app.core.provider_observability`에서 import하면 Python이 먼저 `app.core.__init__`을 실행합니다. 이 모듈은 import 시점에 `get_config()`를 호출하므로 타입 사용만으로도 Backend 전용 DB 설정을 요구합니다.

Worker 전용 타입을 다시 정의하면 Backend와 Worker의 Provider·operation·trace 검증이 분기됩니다. Worker Provider 경로에서 `observability_disabled=true`를 사용하면 #211의 fail-closed 정책을 우회합니다. 두 방식 모두 허용하지 않습니다.

## 3. 결정

저장소 루트에 데이터 계약만 포함하는 `provider_contracts` Python package를 추가합니다.

```text
provider_contracts/
├── __init__.py
└── observability.py
```

`provider_contracts`는 다음에 의존할 수 있습니다.

- Python 표준 라이브러리
- `dataclasses`, `enum`, `uuid`처럼 타입 검증에 필요한 표준 모듈

다음에는 의존할 수 없습니다.

- `app` 또는 `ai_worker`
- Pydantic settings와 환경변수 로더
- SQLAlchemy와 DB 설정
- FastAPI, Redis, OpenAI, HTTP client
- logging 설정과 Provider logger 구현

범용 `shared` package는 만들지 않습니다. `provider_contracts`는 Provider observability 데이터 계약에만 사용하며 다른 공용 유틸리티의 수용 지점으로 확장하지 않습니다.

## 4. 공용 계약

`provider_contracts/observability.py`는 다음 타입을 소유합니다.

```python
class DeploymentEnvironment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class Provider(StrEnum):
    CLOVA_OCR = "CLOVA_OCR"
    OPENAI = "OPENAI"


class ProviderOperation(StrEnum):
    PRESCRIPTION_RECOGNITION = "PRESCRIPTION_RECOGNITION"
    OCR_STRUCTURING = "OCR_STRUCTURING"
    GUIDE_GENERATION = "GUIDE_GENERATION"
    CHAT_GENERATION = "CHAT_GENERATION"


class ProviderFailurePhase(StrEnum):
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"
    TRANSPORT_CONNECTION = "TRANSPORT_CONNECTION"
    HTTP_STATUS = "HTTP_STATUS"
    RESPONSE_VALIDATION = "RESPONSE_VALIDATION"
    PROVIDER_POLICY = "PROVIDER_POLICY"
    APPLICATION_DEADLINE = "APPLICATION_DEADLINE"
    UNKNOWN_INTERNAL = "UNKNOWN_INTERNAL"


class ProviderErrorCode(StrEnum):
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_CONNECTION_FAILED = "PROVIDER_CONNECTION_FAILED"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_REQUEST_REJECTED = "PROVIDER_REQUEST_REJECTED"
    PROVIDER_RESPONSE_INVALID = "PROVIDER_RESPONSE_INVALID"
    PROVIDER_REFUSAL = "PROVIDER_REFUSAL"
    PROVIDER_SAFETY_FILTERED = "PROVIDER_SAFETY_FILTERED"
    PROVIDER_CALL_ABORTED = "PROVIDER_CALL_ABORTED"
    PROVIDER_INTERNAL_FAILURE = "PROVIDER_INTERNAL_FAILURE"


@dataclass(frozen=True)
class ProviderCallContext:
    trace_id: str
    validation_run_id: UUID | None
    environment: DeploymentEnvironment
    validation_enabled: bool


@dataclass(frozen=True)
class ProviderCallDescriptor:
    provider: Provider
    operation: ProviderOperation
    prompt_version: str | None
```

기존 검증 의미를 그대로 이동합니다.

- `trace_id`는 정확히 32자리 hexadecimal 문자열입니다.
- `validation_run_id`가 있으면 `validation_enabled=true`이고 environment가 `local`이어야 합니다.
- CLOVA OCR은 `PRESCRIPTION_RECOGNITION`과 `prompt_version=null`만 허용합니다.
- OpenAI는 `PRESCRIPTION_RECOGNITION`을 사용할 수 없고 비어 있지 않은 `prompt_version`이 필요합니다.
- enum 값, 오류 분류, Provider log schema는 변경하지 않습니다.

## 5. Backend 호환성

`backend/app/core/config.py`는 공용 enum을 다음처럼 alias합니다.

```python
from provider_contracts.observability import DeploymentEnvironment as Env
```

따라서 기존 `from app.core.config import Env` 소비자는 같은 enum 객체를 계속 사용합니다. 별도의 Backend `Env` enum을 유지하지 않습니다.

`backend/app/core/provider_observability.py`는 공용 타입을 import하고 기존 module namespace로 다시 노출합니다. `ProviderCallLogger`, `ProviderCallSpan`, `ProviderCallObserver`와 logger instance는 Backend에 남습니다. 기존 Backend adapter import 경로와 logger 동작, 20개 필드 allowlist, terminal cardinality는 변경하지 않습니다.

## 6. Worker context 생성

`ai_worker/core/provider_observability.py`에 Worker 전용 factory를 추가합니다.

```python
def create_worker_provider_call_context(
    *,
    message: WorkerMessage,
    environment: DeploymentEnvironment,
) -> ProviderCallContext:
    return ProviderCallContext(
        trace_id=message.trace_id,
        validation_run_id=None,
        environment=environment,
        validation_enabled=False,
    )
```

factory는 `WorkerMessage` 검증을 통과한 `trace_id`를 변경하거나 새로 생성하지 않습니다. `validation_run_id`와 `validation_enabled`는 현재 비동기 Worker 계약에서 각각 `None`, `False`로 고정하며 설정으로 열지 않습니다.

`ai_worker/core/config.py`는 다음 필드를 가집니다.

```python
ENV: DeploymentEnvironment
```

`ENV`는 Worker 실행 환경이 반드시 제공해야 하는 필수값이며 추정하거나 local로 기본 설정하지 않습니다. 허용값은 `local`, `staging`, `production`뿐입니다. Worker context factory는 설정 객체 전체를 받지 않고 검증된 enum 값만 받습니다.

## 7. Docker와 실행 경계

Backend와 Worker Dockerfile은 각각 저장소 루트 build context에서 다음 package를 명시적으로 복사합니다.

```dockerfile
COPY ./provider_contracts ./provider_contracts
```

Backend image와 Worker image 모두 `/app/provider_contracts`에서 같은 소스를 import합니다. 새 dependency나 package installation 단계는 추가하지 않습니다.

Compose의 Provider credential, DB credential, staging·production 설정은 변경하지 않습니다. 공용 package를 추가하기 위해 Worker에 Backend 소스나 Backend DB 설정을 새로 복사·주입하지 않습니다.

## 8. #232와의 책임 경계

#231은 다음까지만 구현합니다.

- 공용 타입과 검증 규칙
- Backend 기존 import 호환성
- Worker `ENV` 설정과 context factory
- 두 image의 공용 package 포함
- import·context·기존 observability 회귀 테스트

#232는 다음을 소유합니다.

- 실제 CLOVA adapter를 Worker runtime에 제공하는 조립 방식
- OCR Job 조회와 Provider 입력 구성
- OCR 결과 정규화와 `HandlerSuccess`·실패 매핑
- #141 실행 경계와의 통합

#232는 #231의 factory와 descriptor를 사용하며 공용 타입을 다시 정의하거나 `observability_disabled=true`를 사용하지 않습니다.

## 9. 오류와 보안 경계

- 공용 타입 검증 실패에는 payload, OCR 원문, API Key, Secret, DB credential을 포함하지 않습니다.
- factory는 메시지 전체나 Provider payload를 로그로 남기지 않습니다.
- `validation_run_id`는 Worker context에 입력받지 않습니다.
- Provider request·response ID의 local validation 허용 조건은 변경하지 않습니다.
- 실제 환자정보나 의료문서 fixture를 추가하지 않습니다.
- 실제 CLOVA·OpenAI 호출은 설계·자동 테스트에서 실행하지 않습니다.

## 10. 테스트 설계

### 공용 계약

- 기존 context·descriptor의 모든 유효·무효 조합이 이동 후에도 동일하게 동작합니다.
- Backend compatibility import가 공용 타입과 동일한 객체를 가리킵니다.

### import 격리

DB 환경변수를 제거한 subprocess에서 `provider_contracts.observability` import와 context 생성이 성공해야 합니다. 같은 subprocess에서 `app.core`와 `ai_worker.core`가 `sys.modules`에 없어야 합니다.

### Worker 설정과 factory

- `ENV=local|staging|production`을 공용 enum으로 파싱합니다.
- `ENV` 누락과 허용 목록 밖 값은 Config 생성 시 실패합니다.
- factory 결과는 메시지 trace와 Worker environment를 보존합니다.
- factory 결과의 `validation_run_id`는 `None`, `validation_enabled`는 `False`입니다.

### Docker 계약

- Backend Dockerfile과 Worker Dockerfile이 `provider_contracts`를 복사합니다.
- 기존 Backend image probe가 공용 enum 이동 후에도 성공합니다.

### 회귀

- HTTP dependency wiring의 active context·descriptor 강제
- `observability_disabled` 충돌 조합 거부
- Provider started 1건·terminal 최대 1건
- 20개 로그 필드 allowlist와 금지정보 비노출
- 전체 Mock 기반 테스트, Ruff, mypy

## 11. 비목표

- 공개 API, DB schema·migration, Stream message schema 변경
- `WorkerMessage.trace_id` 변경
- Provider adapter·Handler·logger의 Worker runtime 조립
- Provider payload, timeout, retry, 오류 code 변경
- `provider-call-log-v1` schema 변경
- Worker local Live validation 지원
- staging·production 배포 설정 변경
- 실제 Provider Live 호출

## 12. 완료 조건

- Worker가 Backend DB 환경변수 없이 공용 계약을 import하고 context를 생성합니다.
- 공용 package import가 Backend 또는 Worker 설정 초기화를 실행하지 않습니다.
- Backend 기존 import와 runtime 동작이 유지됩니다.
- Worker environment와 validation 값의 출처가 코드와 계약에서 일치합니다.
- 두 Docker image가 공용 package를 포함합니다.
- Mock 기반 관련 테스트, 전체 테스트, Ruff, mypy가 통과합니다.
- 실제 Provider Live 호출을 실행하지 않습니다.
