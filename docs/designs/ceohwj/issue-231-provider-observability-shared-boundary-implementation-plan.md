# Issue #231 Provider 관측성 공용 경계 구현 계획

## 목표

Backend와 AI Worker가 Backend 설정이나 DB credential을 끌어오지 않고 하나의 import-neutral
Provider 관측성 데이터 계약을 사용하게 한다.

설계 정본은 `docs/designs/ceohwj/issue-231-provider-observability-shared-boundary-design.md`다.

## 전역 제약

- 공개 API, DB schema, Stream message schema, Provider payload, timeout, retry, 오류 의미를 바꾸지 않는다.
- Worker Provider adapter와 OCR Handler wiring은 `#232`에 남긴다.
- Worker 관측성 opt-out이나 공용 타입 복제를 추가하지 않는다.
- staging·production 배포 설정을 바꾸지 않는다.
- credential, Provider payload, OCR text, 실제 의료 데이터를 코드·테스트 출력에 넣지 않는다.
- 실제 CLOVA·OpenAI 호출 없이 모든 Provider 테스트를 mock으로 수행한다.

## Task 1: Import-neutral 공용 계약

- `tests/contract/test_provider_observability_shared_boundary.py`에 기존 context·descriptor의 정상·실패
  불변식과 import 격리 테스트를 먼저 추가한다.
- `provider_contracts/__init__.py`, `provider_contracts/observability.py`에 enum과 frozen dataclass를 옮긴다.
- 표준 라이브러리 외 의존성이 없고 Backend·Worker core가 함께 import되지 않는지 확인한다.

## Task 2: Backend 호환성 보존

- `app.core.config.Env`와 Provider 타입이 공용 객체와 같은 identity인지 실패 테스트로 고정한다.
- Backend `Env`를 공용 enum alias로 바꾸고 관측성 타입은 공용 package에서 import·re-export한다.
- `ProviderCallLogger`, span, observer, 20-field allowlist와 terminal-event logic은 변경하지 않는다.
- Backend config·관측성·adapter·dependency wiring 테스트를 실행한다.

## Task 3: Worker 환경과 안전한 context

- Worker가 `local`, `staging`, `production`을 parsing하고 ENV 누락·오류를 거부하는지 테스트한다.
- validated message trace와 명시적 environment로 공용 context를 만드는 factory를 테스트한다.
- Worker `Config.ENV`를 기본값 없는 필드로 추가하고 관련 테스트에 `ENV="local"`을 명시한다.
- factory는 Backend 코드, logger, Provider adapter를 import하지 않는다.

## Task 4: 두 image에 공용 package 포함

- Backend·Worker Dockerfile이 `provider_contracts`를 `/app/provider_contracts`로 복사하는지 계약 테스트를 추가한다.
- 두 Dockerfile에 같은 `COPY ./provider_contracts ./provider_contracts`를 추가한다.
- dependency, credential, command, 배포 환경값은 바꾸지 않는다.

## Task 5: 통합 검증

```bash
uv run pytest tests/contract/test_provider_observability_shared_boundary.py \
  tests/contract/test_provider_contracts_docker_copy.py -q
uv run pytest backend/app/tests/core backend/app/tests/test_config.py ai_worker/tests/core -q
uv run ruff check provider_contracts backend/app ai_worker tests/contract
uv run mypy provider_contracts backend/app ai_worker
git diff --check
```

변경 범위와 import graph를 검토하고 secret·환자 데이터·Provider payload가 없음을 확인한다.
