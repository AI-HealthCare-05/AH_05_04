# AI One-Cycle Release Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MVP 단계에서 합성 처방 확정부터 실제 OpenAI 가이드·챗봇 응답과 DB 저장까지 한 사이클을 실행별 staging stack에서 검증하고 비민감 기록을 남긴다.

**Architecture:** Issue #67이 merge해 확정한 PostgreSQL driver·service·migration 계약을 선행 입력으로 사용한다. 고유 run ID마다 PostgreSQL volume, FastAPI와 one-off validator로 구성된 Compose project를 만들고 OpenAI key는 FastAPI에만 전달한다. 실행이 끝나면 project와 volume을 통째로 폐기하므로 별도 control DB, 실행 원장, resolver와 migration lock은 만들지 않는다.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, PostgreSQL 17·asyncpg as finalized by Issue #67, OpenAI Responses API, httpx, pytest, Docker Compose

**Design:** `docs/designs/ceohwj/ai-one-cycle-release-validation-design.md`

**Spec:** Issue [#67](https://github.com/AI-HealthCare-05/AH_05_04/issues/67), `docs/deployment.md`, `docs/contracts/medication-guide-ai-backend.md`, `docs/contracts/medication-chat-ai-backend.md`, `docs/testing.md`

## Global Constraints

- 실제 환자·처방·대화 데이터를 사용하지 않는다.
- Issue #67 merge와 PostgreSQL migration·전체 회귀 검사 통과 전에는 Task 1 이후 구현을 시작하지 않는다.
- DB engine·driver·연결 URL, local·production Compose, 공통 env example, Alembic·CI 전환과 데이터 이관은 Issue #67 소유 범위이며 이 계획에서 수정하지 않는다.
- 실제 `OPENAI_API_KEY`는 staging secret store가 wrapper process 환경에 실행 시간 동안만 주입하고 Compose가 FastAPI에만 전달한다. Git·env file·로그·Issue에 남기지 않는다.
- 기존 사용자 API, DTO, application DB schema와 상태 의미를 변경하지 않는다.
- live smoke는 `ENV=staging`, `RELEASE_VALIDATION_ALLOWED=1`, UUID run ID와 Production DB 차단 검사를 통과해야 한다.
- 동일한 full RepoDigest image를 FastAPI와 validator에 사용한다.
- stdout JSON과 Issue 댓글에는 생성 본문, 질문 전문, token과 credential을 포함하지 않는다.
- Backend 검증 완료, 기능 Issue 완료와 Production 배포 승인을 서로 다른 상태로 기록한다.
- Frontend 파일은 `@solia142`의 별도 구현 범위로 남긴다.

---

### Task 0: Issue #67 PostgreSQL 전환 선행조건 확인

**Files:**
- Read only: `pyproject.toml`
- Read only: `app/core/config.py`
- Read only: `infra/docker/docker-compose.prod.yml`
- Read only: `.github/workflows/`
- Read only: `alembic/`
- Read only: `docs/deployment.md`

**Interfaces:**
- Consumes: Issue #67의 merge commit과 CI 결과
- Produces: one-cycle 구현이 의존할 확정 PostgreSQL driver, internal service host·port, image reference와 migration command

- [ ] **Step 1: Issue #67 완료 상태와 merge를 확인한다**

  Run: `gh issue view 67 --repo AI-HealthCare-05/AH_05_04 --json state,closedAt,closedByPullRequestsReferences,url`

  Expected: `state`가 `CLOSED`이고 연결된 PostgreSQL 전환 PR이 `develop`에 merge됨.

- [ ] **Step 2: PostgreSQL 기준이 저장소에 반영됐는지 확인한다**

  Run: `rg -n "asyncpg|postgresql\+asyncpg|pg_isready|5432" pyproject.toml app/core/config.py infra/docker .github scripts/ci docs/deployment.md`
  Run: `if rg -n '"asyncmy' pyproject.toml; then exit 1; fi`

  Expected: driver, SQLAlchemy URL, Compose·CI healthcheck와 내부 포트가 Issue #67의 최종 계약과 일치하며 `asyncmy`를 application dependency로 사용하지 않음.

- [ ] **Step 3: Issue #67 회귀 증거를 확인한다**

  Run: `gh issue view 67 --repo AI-HealthCare-05/AH_05_04 --comments`

  Expected: 빈 PostgreSQL DB의 `alembic upgrade head`, 전체 Backend tests와 CI PASS 증거가 연결됨. 하나라도 없으면 Task 1로 진행하지 않음.

- [ ] **Step 4: one-cycle과 #67 smoke의 완료 증거를 구분한다**

  #67의 핵심 API smoke는 PostgreSQL 전환 후 API 동작 보존 증거로 사용한다. 실제 OpenAI 가이드·챗봇 호출, 실제 모델 ID와 프롬프트 버전 저장은 이 계획의 Task 5에서만 완료로 판정한다.

### Task 1: 실행별 staging stack과 host wrapper 구현

**Files:**
- Create: `envs/example.staging.env`
- Create: `infra/docker/docker-compose.staging.yml`
- Create: `scripts/release_validation/build_staging_image.sh`
- Create: `scripts/release_validation/run_ai_one_cycle_smoke.sh`
- Create: `scripts/release_validation/finalize_result.py`
- Modify: `app/Dockerfile`
- Test: `scripts/release_validation/tests/test_run_ai_one_cycle_smoke.sh`
- Test: `scripts/release_validation/tests/test_build_staging_image.sh`
- Test: `scripts/release_validation/tests/test_finalize_result.py`

**Interfaces:**
- Consumes: Task 0에서 확정한 PostgreSQL image·internal host·port·healthcheck·migration command, UUID run ID, full RepoDigest, `APP_VERSION`, commit SHA, staging secret store의 `OPENAI_API_KEY`
- Produces: 예시 `ah-ai-smoke-00000000000040008000000000000001` 형식의 고유 Compose project, labeled full RepoDigest image, FastAPI readiness, validator stdout JSON, stack teardown 결과

- [ ] **Step 1: wrapper 입력 검증 테스트를 작성한다**

  shell test에서 잘못된 UUID, mutable image tag, 빈 app version, 잘못된 commit SHA가 Compose 실행 전에 거부되는지 확인한다. 전체 compact UUID로 project 이름을 계산하는지, 같은 project label의 container·network·volume이 하나라도 있으면 trap 등록과 fixture 생성 전에 종료하는지도 검사한다. Docker 명령은 PATH 앞쪽의 recording fake로 대체한다.

  ```bash
  run_wrapper --run-id not-a-uuid --image registry/ah-api:latest --app-version "" --commit-sha short
  assert_status 2
  assert_no_recorded_docker_call
  ```

- [ ] **Step 2: 입력 검증 테스트가 실패하는지 확인한다**

  Run: `bash scripts/release_validation/tests/test_run_ai_one_cycle_smoke.sh`

  Expected: wrapper 파일이 없어 FAIL.

- [ ] **Step 3: staging env example을 추가한다**

  실제 값으로 오인되지 않는 placeholder만 사용한다.

  ```dotenv
  ENV=staging
  APP_VERSION=replace-with-staging-version
  DB_HOST=postgres
  DB_PORT=5432
  DB_USER=staging_validation_user
  DB_PASSWORD=replace-with-staging-db-password
  DB_NAME=staging_ai_one_cycle
  SECRET_KEY=replace-with-staging-secret-key
  OPENAI_MODEL=gpt-4o-mini
  OPENAI_TIMEOUT_SECONDS=20
  ```

  실제 `OPENAI_API_KEY`는 이 파일에 저장하지 않는다. staging secret store가 실행 시 wrapper process 환경에만 주입한다. `POSTGRES_IMAGE`도 이 파일에서 임의로 선택하지 않고 Task 0에서 확인한 #67의 immutable image reference를 wrapper가 전달한다.

- [ ] **Step 4: staging Compose를 추가한다**

  `postgres`, `fastapi`, `release-validator`만 정의한다. PostgreSQL image와 healthcheck는 Task 0에서 확인한 #67 최종 계약을 사용하고, one-cycle은 DB driver·URL 조립을 수정하지 않는다. host port, `container_name`, external network와 external volume은 정의하지 않는다. `fastapi`와 validator의 image는 `${RELEASE_VALIDATION_IMAGE:?required}`를 사용하고 validator는 `network_mode: service:fastapi`를 사용한다. 실제 `OPENAI_API_KEY`는 shell 환경에서 interpolation되어 `fastapi.environment`에만 있어야 한다.

  ```yaml
  services:
    postgres:
      image: ${POSTGRES_IMAGE:?required}
      environment:
        POSTGRES_DB: ${DB_NAME:?required}
        POSTGRES_USER: ${DB_USER:?required}
        POSTGRES_PASSWORD: ${DB_PASSWORD:?required}
      volumes:
        - postgres-data:/var/lib/postgresql/data
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U \"$$POSTGRES_USER\" -d \"$$POSTGRES_DB\""]
        interval: 2s
        timeout: 3s
        retries: 30
    fastapi:
      image: ${RELEASE_VALIDATION_IMAGE:?required}
      environment:
        ENV: staging
        SECRET_KEY: ${SECRET_KEY:?required}
        DB_HOST: postgres
        DB_PORT: "5432"
        DB_USER: ${DB_USER:?required}
        DB_PASSWORD: ${DB_PASSWORD:?required}
        DB_NAME: ${DB_NAME:?required}
        OPENAI_MODEL: ${OPENAI_MODEL:?required}
        OPENAI_TIMEOUT_SECONDS: ${OPENAI_TIMEOUT_SECONDS:?required}
        OPENAI_API_KEY: ${OPENAI_API_KEY:?required}
      depends_on:
        postgres:
          condition: service_healthy
    release-validator:
      image: ${RELEASE_VALIDATION_IMAGE:?required}
      network_mode: service:fastapi
      environment:
        ENV: staging
        RELEASE_VALIDATION_ALLOWED: "1"
        RELEASE_VALIDATION_RUN_ID: ${RELEASE_VALIDATION_RUN_ID:?required}
        RELEASE_VALIDATION_REPO_DIGEST: ${RELEASE_VALIDATION_REPO_DIGEST:?required}
        DB_HOST: postgres
        DB_PORT: "5432"
        DB_USER: ${DB_USER:?required}
        DB_PASSWORD: ${DB_PASSWORD:?required}
        DB_NAME: ${DB_NAME:?required}
        OPENAI_MODEL: ${OPENAI_MODEL:?required}
        OPENAI_TIMEOUT_SECONDS: ${OPENAI_TIMEOUT_SECONDS:?required}
      depends_on:
        fastapi:
          condition: service_started
  volumes:
    postgres-data:
  ```

- [ ] **Step 5: image provenance를 Dockerfile에 포함한다**

  `DEPLOY_COMMIT_SHA`와 `APP_VERSION` build arg를 runtime env, OCI revision과 version label로 기록한다.

  ```dockerfile
  ARG DEPLOY_COMMIT_SHA
  ARG APP_VERSION
  ENV DEPLOY_COMMIT_SHA=${DEPLOY_COMMIT_SHA}
  ENV APP_VERSION=${APP_VERSION}
  LABEL org.opencontainers.image.revision=${DEPLOY_COMMIT_SHA}
  LABEL org.opencontainers.image.version=${APP_VERSION}
  ```

- [ ] **Step 6: staging image build wrapper를 구현한다**

  build wrapper는 40자리 commit SHA와 non-empty app version을 검사하고 두 값을 build arg로 전달한다. push 결과의 full RepoDigest와 OCI label을 inspect하여 입력과 일치할 때만 digest를 stdout에 출력한다.

  ```bash
  docker build --platform linux/amd64 \
    --build-arg "DEPLOY_COMMIT_SHA=$commit_sha" \
    --build-arg "APP_VERSION=$app_version" \
    -t "$repository:app-$app_version" -f app/Dockerfile .
  docker push "$repository:app-$app_version"
  ```

- [ ] **Step 7: 실행 wrapper를 구현한다**

  wrapper는 UUID에서 하이픈을 제거한 전체 32자를 `RUN_ID_COMPACT`로 사용하는 단일 project-name 함수를 둔다. normal mode는 `docker image inspect`로 RepoDigest, OCI revision과 OCI version을 확인하고 입력 commit SHA·app version과 비교한다. Task 0에서 확인한 immutable PostgreSQL image, 검증된 run ID와 full application RepoDigest를 각각 `POSTGRES_IMAGE`, `RELEASE_VALIDATION_RUN_ID`, `RELEASE_VALIDATION_REPO_DIGEST`로 Compose process에 전달하되 application image에 bake된 `APP_VERSION`과 `DEPLOY_COMMIT_SHA`는 환경변수 override로 덮어쓰지 않는다. image validation이나 project collision처럼 handler 등록 전 실패하면 finalizer를 직접 호출해 `stack_teardown=NOT_RUN` 최소 실패 JSON을 출력한다. 동일 project label의 container·network·volume이 없는 것을 확인한 뒤에만 EXIT·INT·TERM handler를 등록한다. `postgres`와 `fastapi`만 `up -d`하고, validator service로 migration과 `GET /api/openapi.json` readiness를 확인한 다음 one-cycle CLI를 실행한다. `--teardown-only --run-id "00000000-0000-4000-8000-000000000001"` mode는 같은 project-name 함수를 사용하며 정확히 일치하는 기존 project만 폐기하고 fixture나 FastAPI를 시작하지 않는다. teardown helper는 실제 `.staging.env`와 secret을 읽지 않고 committed `envs/example.staging.env`와 비밀이 아닌 고정 dummy interpolation 값만 사용한다.

  ```bash
  project_name="ah-ai-smoke-${run_id_compact}"
  cleanup_done=0
  cleanup() {
    test "$cleanup_done" -eq 0 || return 0
    cleanup_done=1
    OPENAI_API_KEY=teardown-not-a-real-key \
    RELEASE_VALIDATION_IMAGE=invalid.local/teardown@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
    POSTGRES_IMAGE=invalid.local/postgres@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
    RELEASE_VALIDATION_RUN_ID="$run_id" \
    RELEASE_VALIDATION_REPO_DIGEST=invalid.local/teardown@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
    docker compose --project-name "$project_name" --project-directory infra/docker \
      --env-file envs/example.staging.env -f infra/docker/docker-compose.staging.yml \
      down --volumes --remove-orphans
  }
  on_exit() {
    original_status=$?
    trap - EXIT INT TERM
    cleanup || cleanup_status=$?
    finalize_result "$original_status" "${cleanup_status:-0}"
    test "${cleanup_status:-0}" -eq 0 || exit 1
    exit "$original_status"
  }
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  ```

- [ ] **Step 8: teardown 뒤 최종 JSON을 만드는 finalizer를 구현한다**

  Python 표준 라이브러리만 사용해 validator JSON 파일과 teardown 결과를 읽는다. 필수 key를 확인하고 `steps.stack_teardown`을 추가한 뒤 필수 실행 단계와 teardown이 모두 PASS일 때만 `overall=PASS`로 출력한다. validator 실행 전에 실패했거나 JSON 파일이 없거나 잘못된 경우에는 wrapper가 전달한 `run_id`, provenance와 현재 `failure_stage`로 최소 실패 JSON을 만든다. `failure_stage`는 설계 문서의 12개 허용값 또는 null만 받고 project 생성 전 실패는 `stack_teardown=NOT_RUN`으로 기록한다.

  ```python
  result["steps"]["stack_teardown"] = teardown_status
  result["overall"] = "PASS" if execution_passed and teardown_status == "PASS" else "FAIL"
  print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
  ```

- [ ] **Step 9: Compose와 secret 경계를 결정적으로 검사한다**

  Run: `OPENAI_API_KEY=not-a-real-key POSTGRES_IMAGE=invalid.local/postgres@sha256:0000000000000000000000000000000000000000000000000000000000000000 RELEASE_VALIDATION_IMAGE=registry.example/ah-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef RELEASE_VALIDATION_RUN_ID=00000000-0000-4000-8000-000000000001 RELEASE_VALIDATION_REPO_DIGEST=registry.example/ah-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef docker compose --project-name ah-ai-smoke-render --project-directory infra/docker --env-file envs/example.staging.env -f infra/docker/docker-compose.staging.yml config`

  Expected: 비밀이 아닌 example 값만 사용한 render에서 세 service와 project 전용 volume이 생성되고 PostgreSQL bootstrap 값·`pg_isready`, application `DB_*`, 고정 `SECRET_KEY`가 연결되며 dummy `OPENAI_API_KEY`는 FastAPI에만 존재한다. host port, `container_name`, external network·volume은 없다. 실제 staging secret을 사용하는 `docker compose config` 출력은 로그나 Issue에 남기지 않는다.

- [ ] **Step 10: build·run wrapper와 finalizer 테스트를 통과시킨다**

  Run: `bash scripts/release_validation/tests/test_build_staging_image.sh && bash scripts/release_validation/tests/test_run_ai_one_cycle_smoke.sh && uv run pytest scripts/release_validation/tests/test_finalize_result.py -q`

  Expected: build arg·OCI label·RepoDigest 검증, 전체 UUID project 이름, 기존 project 충돌 거부, DB health→migration→readiness→validator 순서, migration/readiness/validator 실패, INT·TERM, teardown 실패와 최소 실패 JSON 테스트 PASS.

### Task 2: one-cycle validator CLI 구현

**Files:**
- Create: `app/release_validation/__init__.py`
- Create: `app/release_validation/ai_one_cycle_smoke.py`
- Create: `app/tests/release_validation/__init__.py`
- Create: `app/tests/release_validation/test_ai_one_cycle_smoke.py`

**Interfaces:**
- Consumes: `ENV`, `RELEASE_VALIDATION_ALLOWED`, run ID, `APP_VERSION`, `DEPLOY_COMMIT_SHA`, full RepoDigest, staging DB session factory, `http://127.0.0.1:8000/api/v1`
- Produces: `python -m app.release_validation.ai_one_cycle_smoke`의 strict JSON stdout과 종료 코드

- [ ] **Step 1: 환경 guard 실패 테스트를 작성한다**

  다음 중 하나라도 해당하면 fixture 생성 전에 exit code `2`가 되는 parametrized test를 작성한다.

  ```python
  @pytest.mark.parametrize(
      "override",
      [
          {"ENV": "production"},
          {"RELEASE_VALIDATION_ALLOWED": "0"},
          {"RELEASE_VALIDATION_RUN_ID": "invalid"},
          {"APP_VERSION": ""},
          {"DEPLOY_COMMIT_SHA": "short"},
          {"RELEASE_VALIDATION_REPO_DIGEST": "registry/ah-api:latest"},
          {"OPENAI_MODEL": "gpt-4.1"},
          {"DB_NAME": "production"},
      ],
  )
  def test_environment_guard_rejects_unsafe_input(override): ...
  ```

- [ ] **Step 2: 환경 guard 테스트가 실패하는지 확인한다**

  Run: `uv run pytest app/tests/release_validation/test_ai_one_cycle_smoke.py -k environment_guard -q`

  Expected: module import 실패.

- [ ] **Step 3: 최소 guard와 결과 type을 구현한다**

  `SmokeStepStatus`는 `PASS | FAIL | NOT_RUN`, validator의 `SmokeExecutionResult.execution`은 `PASS | FAIL`만 허용한다. Production host·DB deny-list, `staging_` DB prefix, UUID, 40자리 commit SHA, full RepoDigest와 `OPENAI_MODEL == "gpt-4o-mini"`를 확인한다.

  ```python
  class SmokeStepStatus(StrEnum):
      PASS = "PASS"
      FAIL = "FAIL"
      NOT_RUN = "NOT_RUN"

  class FailureStage(StrEnum):
      IMAGE_VALIDATION = "IMAGE_VALIDATION"
      PROJECT_COLLISION = "PROJECT_COLLISION"
      STACK_START = "STACK_START"
      DATABASE_MIGRATION = "DATABASE_MIGRATION"
      FASTAPI_READINESS = "FASTAPI_READINESS"
      PRESCRIPTION_CONFIRMATION = "PRESCRIPTION_CONFIRMATION"
      GUIDE_GENERATION = "GUIDE_GENERATION"
      CHAT_SESSION = "CHAT_SESSION"
      CHAT_RESPONSE = "CHAT_RESPONSE"
      DATABASE_VERIFICATION = "DATABASE_VERIFICATION"
      STACK_TEARDOWN = "STACK_TEARDOWN"
      RESULT_FINALIZATION = "RESULT_FINALIZATION"

  class SmokeExecutionResult(BaseModel):
      run_id: UUID
      environment: Literal["staging"]
      app_version: str
      commit_sha: str
      image_repo_digest: str
      execution: Literal["PASS", "FAIL"]
      failure_stage: FailureStage | None
  ```

- [ ] **Step 4: 합성 fixture builder 테스트를 작성한다**

  fixture commit 결과가 run ID 기반 고유 사용자, `MedicalDocument`, `OcrJob(COMPLETED)`와 모두 `CONFIRMED`인 `ExtractedField`를 만드는지 확인한다. 처방일 `2026-08-21`, 약물명 `합성의약품 에이`, 복용량 `1`, 단위 `정`, 횟수 `2`, 시점 `식후`, 기간 `3`을 exact 값으로 검사한다.

- [ ] **Step 5: 합성 fixture builder를 구현한다**

  기존 model constraint와 password hashing utility를 재사용하고 fixture를 commit한다. 실제 OCR Provider를 호출하지 않는다. 반환 type은 로그인 정보와 `document_id`만 포함하며 실제 credential을 loggable repr에서 제외한다.

- [ ] **Step 6: HTTP orchestration 테스트를 작성한다**

  fake `httpx.AsyncClient`로 다음 호출 순서와 ID 전달을 검사한다.

  ```python
  assert request_paths == [
      "/auth/login",
      f"/documents/{document_id}/prescription",
      "/guides",
      f"/prescriptions/{prescription_id}/chat-sessions",
      f"/chat-sessions/{session_id}/messages",
  ]
  ```

  처방 확정·가이드·채팅 세션·메시지 응답은 `Cache-Control: no-store`와 예상 HTTP 상태를 확인한다.

- [ ] **Step 7: HTTP orchestration을 구현한다**

  connect timeout은 5초, read timeout은 `OPENAI_TIMEOUT_SECONDS + 10`초로 설정한다. token은 지역 변수로만 유지하고 JSON·stderr에 출력하지 않는다. transport 오류, timeout과 비정상 응답은 해당 단계 `FAIL`과 non-zero 종료로 처리한다.

- [ ] **Step 8: 독립 DB 재확인 테스트를 작성한다**

  fixture에 사용한 기존 `AsyncSessionFactory`를 재사용하되 fixture session을 commit·close한 다음 새 `AsyncSession`과 새 transaction이 열리는지 확인한다. 두 session 객체가 다름을 검사하고 GUIDE와 ASSISTANT row에 대해 다음을 exact assertion으로 검사한다.

  ```python
  assert guide.generation_status == GuideGenerationStatus.COMPLETED
  assert guide.content
  assert guide.model_name.startswith("gpt-4o-mini")
  assert guide.prompt_version == "guide-prompt-v1"
  assert guide.error_code is None and guide.error_message is None
  assert guide.completed_at is not None
  ```

  Chat도 `COMPLETED`, non-empty content, `gpt-4o-mini` 계열, `chat-prompt-v1`, null 오류, non-null 완료 시각과 올바른 session 소유 관계를 검사한다.

- [ ] **Step 9: DB verifier와 strict JSON 출력을 구현한다**

  validator stdout에는 실행 JSON 한 건만 쓰고 진단은 stderr에 쓴다. content는 길이만 기록한다. 모든 필수 실행 단계가 PASS일 때만 `execution=PASS`, exit code `0`을 반환한다. 최종 `overall`과 `stack_teardown`은 Task 1의 host finalizer가 추가한다.

- [ ] **Step 10: validator 전용 테스트를 통과시킨다**

  Run: `uv run pytest app/tests/release_validation/test_ai_one_cycle_smoke.py -q`

  Expected: 실제 OpenAI 호출 없이 모든 테스트 PASS.

### Task 3: 실패 상태 저장 증거를 필수 assertion으로 보강

**Files:**
- Modify: `app/tests/guide_ai/test_backend_contract.py`
- Modify: `app/tests/repositories/test_guide_repository.py`
- Verify: `app/tests/chat_apis/test_chat_message_api.py`
- Verify: `app/tests/repositories/test_chat_repository.py`

**Interfaces:**
- Consumes: 기존 Guide·Chat 실패 처리와 repository commit 동작
- Produces: 실제 Provider 장애 없이 재현 가능한 실패 저장 증거

- [ ] **Step 1: 현재 Guide assertion 공백을 재현한다**

  Run: `uv run pytest app/tests/guide_ai/test_backend_contract.py::test_backend_contract_maps_generation_errors_and_marks_failed app/tests/repositories/test_guide_repository.py::test_mark_failed_persists_after_subsequent_rollback -q`

  Expected: 기존 테스트는 PASS하지만 전체 실패 필드 검증이 없어 보강 대상임을 확인한다.

- [ ] **Step 2: Guide backend contract assertion을 추가한다**

  `mark_failed` 호출의 고정 `error_code`, 비어 있지 않은 `error_message`를 확인하고 반환·저장 객체가 실패 상태를 표현하는지 검사한다.

  ```python
  assert repository.mark_failed.await_args.kwargs["error_code"] == stored_error_code
  assert repository.mark_failed.await_args.kwargs["error_message"]
  ```

- [ ] **Step 3: Guide repository 재조회 assertion을 추가한다**

  ```python
  assert guide.generation_status == GuideGenerationStatus.FAILED
  assert guide.error_code == "OPENAI_API_ERROR"
  assert guide.error_message == "고정된 안전 문구"
  assert guide.completed_at is not None
  assert (guide.content, guide.model_name, guide.prompt_version) == (None, None, None)
  ```

- [ ] **Step 4: Guide와 기존 Chat 실패 테스트를 함께 실행한다**

  Run: `uv run pytest app/tests/guide_ai/test_backend_contract.py app/tests/repositories/test_guide_repository.py app/tests/chat_apis/test_chat_message_api.py app/tests/repositories/test_chat_repository.py -q`

  Expected: Guide와 Chat 모두 실패 상태와 null 생성 metadata를 DB 재조회로 확인하며 PASS.

### Task 4: 검증 기준과 사람이 읽을 기록 문서화

**Files:**
- Create: `docs/validation/ai-one-cycle-release.md`
- Modify: `docs/README.md`

**Interfaces:**
- Consumes: validator JSON schema와 staging wrapper 명령
- Produces: 실행 전 준비, 성공 판정, Issue 댓글과 잔존 stack 정리 절차

- [ ] **Step 1: 실행 체크리스트를 작성한다**

  문서에 실행 전 owner 승인, synthetic-only 확인, image RepoDigest·app version·commit SHA, FastAPI-only key, 자동 테스트 결과와 정확한 wrapper 명령을 기록한다.

- [ ] **Step 2: 결과 판정과 Issue 댓글 양식을 작성한다**

  `run_id`, 실행 시각, `app_version`, commit SHA, RepoDigest, Guide·Chat model과 prompt version, 단계별 PASS/FAIL, stack teardown을 필수 필드로 둔다. 생성 본문과 token은 금지한다.

- [ ] **Step 3: 비정상 종료 후 수동 teardown 절차를 작성한다**

  project 이름을 문서에서 다시 계산하지 않고 normal mode와 같은 wrapper 함수를 사용하는 다음 명령만 안내한다.

  ```bash
  scripts/release_validation/run_ai_one_cycle_smoke.sh \
    --teardown-only \
    --run-id "00000000-0000-4000-8000-000000000001"
  ```

- [ ] **Step 4: 문서 index와 whitespace를 확인한다**

  Run: `rg -n "ai-one-cycle-release" docs/README.md docs/validation/ai-one-cycle-release.md && git diff --check`

  Expected: 문서 링크가 검색되고 whitespace 오류가 없음.

### Task 5: staging live smoke와 후속 Frontend 인계

**Files:**
- No repository file modifications for the actual live result
- No Frontend code changes in this task

**Interfaces:**
- Consumes: 자동 검사 통과 image, staging secret, 운영자 UUID run ID
- Produces: Backend/AI one-cycle Issue 댓글과 Frontend 후속 Issue 협업 요청

- [ ] **Step 1: Backend 회귀 검사를 실행한다**

  Run: `uv run ruff check . && uv run ruff format . --check && uv run mypy app ai_worker && bash scripts/ci/run_test.sh`

  Expected: 모두 PASS. PostgreSQL·Docker 등 선행 조건을 충족하지 못한 검사는 생략하지 않고 blocker로 기록하며, live smoke skip은 실제 live 검증 성공으로 간주하지 않는다.

- [ ] **Step 2: 승인된 staging에서 wrapper를 한 번 실행한다**

  Run: `scripts/release_validation/run_ai_one_cycle_smoke.sh --run-id "$RUN_ID" --image "$RELEASE_VALIDATION_IMAGE" --app-version "$APP_VERSION" --commit-sha "$DEPLOY_COMMIT_SHA"`

  Expected: `overall=PASS`, Guide·Chat metadata 확인, `stack_teardown=PASS`, exit code `0`.

- [ ] **Step 3: GitHub Issue에 비민감 결과를 기록한다**

  CLI JSON에서 설계 문서의 Issue 댓글 필드만 옮긴다. 실제 run 결과는 `docs/validation/ai-one-cycle-release.md`에 누적하거나 commit하지 않는다. API key, token, 질문·가이드·답변 전문은 남기지 않는다.

- [ ] **Step 4: Frontend 후속 작업을 인계한다**

  `@solia142`에게 실제 가이드·챗봇 route 구현 후 로그인 → 처방 확정 → 가이드 → 채팅 한 건의 happy-path E2E를 별도 Issue로 요청한다. 해당 E2E는 독립 합성 fixture와 삭제 절차를 사용한다.

- [ ] **Step 5: CODEOWNER 리뷰와 완료 상태를 구분한다**

  Backend `@phina-io`, 배포·아키텍처 `@hazelnutflavoured`, AI `@ceohwj` 리뷰를 받는다. 결과에는 `Backend/AI one-cycle 완료`, `Frontend E2E 대기`, `Production 배포 미승인`을 각각 표시한다.

## Self-Review Result

- 선행 의존성: Issue #67 merge와 PostgreSQL migration·전체 회귀 PASS 전에는 구현을 시작하지 않는다.
- 소유권 경계: DB engine·driver·URL, local·production Compose, 공통 env example, Alembic·CI와 데이터 이관은 `@Jye-rookie`의 #67 범위로 유지한다.
- MVP 범위: 실행별 staging DB를 폐기하므로 control DB, durable ledger, resolver, retention과 migration lock을 포함하지 않는다.
- 계약 경계: 기존 사용자 API·DTO·application DB schema를 변경하지 않는다.
- 비밀 경계: OpenAI key는 FastAPI에만 전달하고 validator와 공개 기록에는 전달하지 않는다.
- 기록: app version, commit SHA, RepoDigest, 실제 모델 ID, 프롬프트 버전, 단계와 teardown 결과를 남긴다.
- 실패 증거: Guide의 누락된 실패 필드 assertion은 조건부가 아니라 Task 3의 필수 작업이다.
- Frontend: 현재 구현이 없으므로 별도 owner-owned 후속 E2E로 유지한다.
