# Issue #231 Provider 관측성 공용 경계 설계

> **상태:** 구현·병합 완료
> **구현 담당자:** 정현우 `@ceohwj`
> **Backend/API·Security 리뷰:** 송은영 `@phina-io`
> **Worker/OCR 리뷰:** 김지혜 `@Jye-rookie`

## 목적

Backend와 AI Worker가 Provider 호출의 context·descriptor·enum을 하나의 import-neutral 계약으로
사용하게 한다. 공용 계약을 import하거나 Worker context를 만들 때 Backend `Config`, DB 설정,
FastAPI, Redis, logger를 초기화하지 않는다.

이 경계는 `#232` OCR Worker Handler가 기존 Provider 관측성의 trace·operation 의미를 재사용하기
위한 선행조건이다. 실제 CLOVA adapter와 OCR Handler 조립은 `#232` 소유다.

## 문제

기존 타입은 `backend/app/core/provider_observability.py`와 `backend/app/core/config.py`에 있어
Worker가 import하면 `app.core.__init__`을 거치며 Backend DB 설정까지 요구했다. Worker 전용 타입을
복제하면 Backend와 Worker의 검증 의미가 갈라지고, `observability_disabled=true` 같은 우회 경로는
`#211` fail-closed 정책을 훼손한다.

## 결정

저장소 루트에 데이터 계약만 포함하는 `provider_contracts` package를 둔다.

```text
provider_contracts/
├── __init__.py
└── observability.py
```

허용 의존성은 Python 표준 라이브러리와 `dataclasses`, `enum`, `uuid`뿐이다. 다음 의존성은 금지한다.

- `app`, `ai_worker`
- Pydantic settings와 환경변수 loader
- SQLAlchemy와 DB 설정
- FastAPI, Redis, OpenAI, HTTP client
- logging 설정과 Provider logger 구현

범용 `shared` package로 확장하지 않고 Provider 관측성 데이터 계약에만 사용한다.

## 공용 계약

`provider_contracts/observability.py`는 다음 타입을 소유한다.

- `DeploymentEnvironment`: `local`, `staging`, `production`
- `Provider`: `CLOVA_OCR`, `OPENAI`
- `ProviderOperation`: 처방전 인식, OCR 구조화, Guide 생성, Chat 생성
- `ProviderFailurePhase`: timeout, connection, HTTP status, response validation, policy, deadline, internal
- `ProviderErrorCode`: 안정적인 비민감 Provider 오류 코드
- `ProviderCallContext`: trace·환경·검증 context
- `ProviderCallDescriptor`: Provider·operation·model·endpoint descriptor

모든 값 객체는 `frozen=True, slots=True`이고 생성 시 불변식을 검증한다. Validation error에는 비밀,
Provider payload, OCR text, 환자 데이터가 포함되지 않는다.

## Backend 호환성

- `backend/app/core/config.py`의 `Env`는 `DeploymentEnvironment` alias로 유지한다.
- `backend/app/core/provider_observability.py`는 공용 enum과 dataclass를 re-export한다.
- 기존 import path와 객체 identity를 유지해 API·테스트 호환성을 보존한다.
- logger, span, observer, allowlist, terminal event 의미는 Backend에 남기고 변경하지 않는다.

## Worker context

Worker `Config`는 기본값 없는 `ENV: DeploymentEnvironment`를 요구한다. Worker factory는 검증된
`WorkerMessage`의 trace와 환경을 사용해 `ProviderCallContext`를 만들며 다음 값을 고정한다.

- `validation_run_id=None`
- `validation_enabled=False`

Backend 코드·DB·logger를 import하지 않고, 관측성을 끄는 선택지를 제공하지 않는다.

## Docker와 책임 경계

Backend와 Worker image는 `provider_contracts`를 `/app/provider_contracts`로 명시적으로 복사한다.
dependency나 runtime command, secret, deployment 환경값은 변경하지 않는다.

`#231`은 공용 타입과 context factory까지만 소유한다. `#232`가 소유하는 영역은 다음과 같다.

- CLOVA Provider adapter 호출
- OCR Handler와 observer 조립
- runtime span 종료와 오류 mapping
- 실제 Provider 호출 흐름 검증

## 테스트와 완료 조건

- 모든 공용 값 객체의 정상·실패 불변식을 검증한다.
- subprocess에서 Backend DB 환경 없이 import하고 `app.core`, `ai_worker.core`가 load되지 않음을 확인한다.
- Backend alias identity와 기존 관측성 동작을 회귀 검증한다.
- Worker `ENV` parsing과 context factory를 mock 기반으로 검증한다.
- 두 Dockerfile이 공용 package를 복사하는지 계약 테스트로 검증한다.
- 실제 CLOVA/OpenAI 호출, DB schema, API, Stream message, retry·timeout 변경은 하지 않는다.
