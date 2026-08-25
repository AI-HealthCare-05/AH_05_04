# AI One-Cycle Release Validation Implementation Plan

| 항목 | 내용 |
| --- | --- |
| 관련 Issue/PR | 미정 — 실행 전 Backend·Infrastructure·Frontend 소유자 합의 필요 |
| 문서 상태 | Draft / Blocked — Task 0의 staging 경계가 구현되기 전 Task 2 이후 실행 금지 |
| 실행 추적 | Issue 생성 후 checklist를 Issue로 옮기고 이 문서는 설계 이력으로 고정 |
| 완료 의미 | Backend 통합 smoke 증거. 기능 완료·Production 배포 승인과 분리 |

**Goal:** 격리된 staging Backend에서 비식별 합성 처방을 확정한 뒤 실제 OpenAI 가이드 생성과 챗봇 응답까지 한 사이클을 실행하고, 성공·실패 영속화를 Backend 통합 증거로 남긴다.

**Architecture:** 실제 OpenAI 호출은 전용 staging FastAPI가 수행한다. 검증 CLI는 같은 immutable app image의 `release-validator` one-off service에서 실행하고 `network_mode: service:fastapi`로 loopback API를 호출하며, application DB validation credential과 별도 control DB credential로 fixture·원장 상태를 관리한다. FastAPI application account와 host wrapper는 control DB에 접근하지 않는다. 응답 유실·프로세스 중단 이후에는 pinned read-only resolver가 run ID로 원래 image provenance를 반환하고, 동일 validation CLI를 그 image의 DB-only `release-cleanup` service에서 실행한다. 실패 저장은 실제 장애를 유도하지 않고 기존 결정적 Backend 테스트로 증명한다.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, PostgreSQL 17, OpenAI Responses API, httpx, pytest, Docker Compose

**Design:** `docs/designs/ceohwj/ai-one-cycle-release-validation-design.md`

**Spec:** `docs/deployment.md`, `docs/contracts/current/medication-guide-ai-backend.md`, `docs/contracts/current/medication-chat-ai-backend.md`, `docs/contracts/proposed/operations/release-validation-ledger.md`, `docs/testing.md`

## Global Constraints

- 실제 환자·처방·대화 데이터를 사용하지 않고 코드에 고정된 비식별 합성 값만 사용한다.
- `OPENAI_API_KEY`는 staging 배포 비밀 저장소에서 FastAPI 컨테이너에만 주입하며 저장소, CLI 인자, 로그, 결과 파일에 기록하지 않는다.
- live smoke는 전용 staging Compose·DB에서 `ENV=staging`, `RELEASE_VALIDATION_ALLOWED=1`, UUID run ID와 DB identity 검사를 모두 통과해야 한다.
- 외부 API·DTO·DB schema·상태 의미는 변경하지 않는다. 변경 필요성이 발견되면 구현을 중단하고 관련 CODEOWNER와 별도 계약 변경으로 분리한다.
- `app/` 구현과 테스트는 `@phina-io`, Frontend 구현과 E2E는 `@solia142`, AI 검증 기준과 합성 시나리오는 `@ceohwj`가 책임지고 공동 리뷰한다.
- 생성 본문, 질문 전문, access token, refresh token, DB 비밀번호, API key를 smoke 출력에 포함하지 않는다.
- Backend 검증 완료, 기능 Issue 완료와 Production 배포 승인을 서로 다른 상태로 관리한다.

---

### Task 0: 격리된 staging 실행 경계 확정

**Files:**
- Create: `envs/example.staging.env`
- Create: `infra/docker/docker-compose.staging.yml`
- Create: `infra/docker/init/staging-release-control.sql`
- Create: `infra/docker/release-control.Dockerfile`
- Create: `scripts/release_validation/run_ai_one_cycle_smoke.sh`
- Create: `scripts/release_validation/run_staging_migration.sh`
- Modify: `docs/contracts/proposed/operations/release-validation-ledger.md` (Proposed → Implemented 상태와 실제 SQL type·길이 확정)
- Modify: `docs/contracts/README.md`
- Modify: `app/Dockerfile`
- Review jointly: staging application DB·control DB·secret-store provisioning outside this repository

- [ ] **Step 1: 전용 staging project와 DB를 정의한다**

  local·production Compose를 재명명해 사용하지 않는다. `ah-staging` Compose project, staging 전용 application DB host·database·최소권한 user를 정의하고 Production DB host·이름·credential을 재사용하지 않는다. 애플리케이션 schema 밖의 staging control DB에 authoritative `release_validation_runs` 원장과 append-only event를 둔다. schema version, 상태·전이, crash recovery, 90일 보존과 role별 권한은 `docs/contracts/proposed/operations/release-validation-ledger.md`를 기준으로 구현한다. 동일 validation CLI의 normal·cleanup role만 상태를 전이하고 resolver는 provenance read-only, migration은 lock·unresolved 조회, retention role은 만료된 `RESOLVED` record·event 삭제만 수행한다. FastAPI application account와 host wrapper에는 control DB 권한을 주지 않는다.

- [ ] **Step 2: normal·cleanup guard를 분리한다**

  공통으로 `ENV=staging`, UUID run ID, `SELECT DATABASE()` exact match와 `staging_` prefix, Production host·DB deny-list, application·control schema compatibility를 확인한다. normal CLI는 run ID 미존재와 FastAPI readiness·provenance·`RELEASE_VALIDATION_ALLOWED=1`을 확인하고 app DB write 전에 원장 record를 만든다. cleanup-only는 기존 원장·provenance 일치를 요구하고 OpenAI·FastAPI 없이 DB-only credential과 `RELEASE_CLEANUP_ALLOWED=1`만 추가로 요구한다.

- [ ] **Step 3: FastAPI 전용 secret allowlist를 적용한다**

  staging OpenAI key는 FastAPI에만 주입하고 AI Worker·Frontend·공통 env 파일·`release-validator`·`release-cleanup`·`release-ledger-resolver` service에는 넣지 않는다. `release-validator`는 application validation·control credential과 `network_mode: service:fastapi`만 사용하고, cleanup service는 같은 immutable app image와 DB-only application·control credential을 사용한다. Resolver는 별도 digest로 고정된 ops image와 provenance read-only control credential만 사용한다. one-off service는 외부 port를 열지 않는다.

- [ ] **Step 4: 실행 중인 image provenance를 고정한다**

  image build arg에서 `DEPLOY_COMMIT_SHA`와 OCI revision label을 bake한다. host wrapper는 `docker inspect`로 local image ID, pull 가능한 full RepoDigest와 revision label을 각각 읽어 전달하고, CLI는 전달된 revision을 bake된 `DEPLOY_COMMIT_SHA`와 비교한다. RepoDigest가 없거나 세 값이 누락·불일치하면 normal 실행을 차단한다.

- [ ] **Step 5: staging 구성 검증을 통과시킨다**

  Compose render, `release-validator`의 network namespace, resolver image digest pin, Production deny-list, 잘못된 DB identity, schema revision 불일치, provenance mismatch와 service별 secret 전달 범위에 대한 결정적 검사를 추가한다. normal·cleanup CLI와 migration process는 control DB의 같은 advisory lock을 전체 작업 동안 유지한다. lock 획득 timeout·실패와 실행 중 connection loss는 다음 app DB write를 중단하는 fail-closed 경로로 처리한다. migration은 lock 안에서 authoritative 원장의 unresolved record 0건을 확인한 뒤 실행하며, unresolved run 생성과 migration의 check-then-act race가 불가능함을 검사한다. State update·event insert의 동일 transaction, 모든 event `UPDATE` 거부, 90일 이전·unresolved 삭제 거부와 eligible event·record 동시 삭제도 검증한다.

### Task 1: Backend 통합 검증 기준 고정

**Files:**
- Create: `docs/validation/ai-one-cycle-release.md`
- Modify: `docs/README.md`

**Interfaces:**
- Consumes: 기존 Guide/Chat 계약의 상태와 metadata 필드
- Produces: Backend 통합 검증 성공·실패 체크리스트. Production 승인 checklist와 분리

- [ ] **Step 1: 검증 행렬을 문서화한다**

  아래 항목을 `docs/validation/ai-one-cycle-release.md`에 표로 기록한다.

  | 단계 | 호출/증거 | 통과 기준 |
  | --- | --- | --- |
  | 처방 확정 | `POST /api/v1/documents/{document_id}/prescription` | `201`, 합성 약물 순서·값 일치 |
  | 가이드 생성 | `POST /api/v1/guides` 및 GUIDE 재조회 | `COMPLETED`, 본문 비어 있지 않음, `model_name.startswith("gpt-4o-mini")`, `prompt_version=guide-prompt-v1` |
  | 채팅 세션 | `POST /api/v1/prescriptions/{prescription_id}/chat-sessions` | `201`, `ACTIVE` |
  | 질문·응답 | `POST /api/v1/chat-sessions/{session_id}/messages` 및 메시지 목록 재조회 | USER/ASSISTANT 한 쌍, ASSISTANT `COMPLETED`, `model_name.startswith("gpt-4o-mini")`, `prompt_version=chat-prompt-v1` |
  | 실패 저장 | 결정적 Backend 회귀 테스트 | GUIDE `FAILED` 및 안전한 오류 코드, Chat USER + FAILED ASSISTANT 쌍 영속화 |
  | 보안 | 배포 환경·로그 점검 | key/token/생성 본문 미노출, 응답 `Cache-Control: no-store` |

- [ ] **Step 2: Backend 검증 차단 조건을 명시한다**

  다음 중 하나라도 발생하면 Backend 통합 검증 실패로 기록한다: 실제 OpenAI 호출 skip, 모델 family 또는 프롬프트 버전 불일치, 처방 약물과 가이드 약물 불일치, ASSISTANT 비완료 상태, 실패 row 미영속화, provenance 누락, 비밀정보 또는 생성 본문 로그 노출, cleanup 또는 사후 0건 검증 실패.

  별도로 `Backend 검증 완료`, `기능 Issue 완료`, `Production 배포 승인` 세 상태를 문서화한다. 새 one-cycle은 기존 Guide·Chat live smoke와 `docs/deployment.md`의 의료 안전·외부 전송·인프라 gate를 대체하지 않는다.

- [ ] **Step 3: 문서 링크를 검증한다**

  Run: `rg -n "ai-one-cycle-release" docs/README.md docs/validation/ai-one-cycle-release.md`

  Expected: 두 파일에서 새 검증 문서 경로가 검색된다.

- [ ] **Step 4: 문서 diff를 검증한다**

  Run: `git diff --check && git diff -- docs/validation/ai-one-cycle-release.md docs/README.md`

  Expected: whitespace 오류가 없고 실제 데이터·비밀값이 없다.

### Task 2: 배포 컨테이너용 one-cycle smoke 명령 구현

**Files:**
- Create: `app/release_validation/__init__.py`
- Create: `app/release_validation/ai_one_cycle_smoke.py`
- Create: `app/tests/release_validation/test_ai_one_cycle_smoke.py`

**Interfaces:**
- Consumes: `config.ENV`, `config.OPENAI_MODEL`, `RELEASE_VALIDATION_ALLOWED`, operator-provided run ID, staging DB identity, authoritative run ledger, baked commit SHA, running full RepoDigest·local image ID, wrapper-inspected OCI revision, `http://127.0.0.1:8000/api/v1`
- Produces: `python -m app.release_validation.ai_one_cycle_smoke` 종료 코드와 비민감 JSON 요약

- [ ] **Step 1: 환경 가드 실패 테스트를 작성한다**

  normal guard는 `ENV != staging`, 실행 허용값 누락, run ID 형식 오류, DB name·host 불일치, Production deny-list 일치, Alembic head 불일치, baked commit SHA·`RELEASE_VALIDATION_IMAGE_REVISION`·`RELEASE_VALIDATION_REPO_DIGEST`·local image ID 누락 또는 불일치, `OPENAI_MODEL != gpt-4o-mini`를 검사한다. OpenAI Key는 validation CLI에 전달하지 않고 FastAPI secret 주입 범위와 실제 호출로 검증한다. cleanup guard는 DB identity·deny-list·schema compatibility·원장 run ID·full RepoDigest·revision·cleanup 허용값·DB-only credential만 검사하고 OpenAI·FastAPI 부재를 허용한다.

- [ ] **Step 2: 환경 가드 테스트가 실패하는지 확인한다**

  Run: `uv run pytest app/tests/release_validation/test_ai_one_cycle_smoke.py -k environment_guard -q`

  Expected: 모듈이 아직 없어 collection 또는 import 실패.

- [ ] **Step 3: 최소 합성 fixture builder를 구현한다**

  운영자가 전달한 run ID로 합성 사용자, `MedicalDocument`, `OcrJob(COMPLETED)`, 전부 `CONFIRMED`인 `ExtractedField`를 생성한다. User의 길이·unique 필드, 양수 file size, OCR terminal timestamp, confirmation timestamp와 medication index constraint를 모두 명시한다. 확인값은 처방일 `2026-08-21`, 약물명 `합성의약품 에이`, 복용량 `1`, 단위 `정`, 횟수 `2`, 시점 `식후`, 기간 `3`이며 `3일`은 저장값이 아니라 렌더링 기대값으로만 사용한다.

- [ ] **Step 4: 실제 HTTP one-cycle orchestration 테스트를 작성한다**

  HTTP client와 DB session을 fake로 주입해 confirm → guide → chat session → message → guide/message 재조회 순서, 각 ID 전달, 의료 endpoint의 `Cache-Control: no-store`, 완료 metadata와 동일한 model-family predicate를 테스트한다. 로그인 응답은 현재 `Cache-Control` 계약 대상이 아니다.

- [ ] **Step 5: one-cycle orchestration을 구현한다**

  `httpx.AsyncClient`로 실제 배포 API를 호출하고 connect timeout 5초, read timeout `OPENAI_TIMEOUT_SECONDS + M` 이상을 명시한다. `M`은 양수인 CLI 처리 여유 설정으로 정의하고 실행 기록에 남긴다. 결과 로그에는 run ID, HTTP 상태, 상태 enum, 실제 모델 ID, 프롬프트 버전, 본문 길이만 남기고 생성 본문과 token은 남기지 않는다. transport 실패는 해당 단계를 `OUTCOME_UNKNOWN`으로 분류한다. 로그인은 안전하게 재시도하고 write endpoint는 exact run anchor·부모 관계에서 terminal row와 ID를 복구한 뒤 다음 미실행 단계부터 계속한다.

- [ ] **Step 6: 저장값을 독립 DB session으로 재조회한다**

  GUIDE와 ASSISTANT row를 API 응답 객체가 아닌 새 DB session에서 조회해 각각 `COMPLETED`, `model_name.startswith("gpt-4o-mini")`, 정확한 `prompt_version`, null `error_code/error_message`를 확인한다. `OUTCOME_UNKNOWN`이면 현재 단계의 run-root 후손을 terminal 상태까지 제한 시간 동안 poll한다. terminal 성공은 해당 단계만 `PASS`, terminal 실패는 execution `FAIL`, timeout·중복 후보·identity 불일치는 execution `OUTCOME_UNKNOWN`이며 모든 필수 단계와 최종 DB 검증이 성공해야 전체 `PASS`다.

- [ ] **Step 7: 합성 row cleanup과 cleanup 실패 처리를 구현한다**

  fixture app DB write 전 원장을 `SETUP_IN_PROGRESS`로 전이하고 exact 예상 anchor를 저장한다. 정상 종료 cleanup 전에는 대상 ID·개수 hash를 저장하고 `CLEANUP_IN_PROGRESS`로 먼저 전이한 뒤 응답 ID가 아니라 exact run anchor의 모든 후손을 FK 역순으로 제거한다. commit 후 새 session에서 0건을 검증한 뒤 원장을 `RESOLVED`로 바꾼다. 강제 종료·응답 유실에 대비해 DB-only service에서 같은 run ID를 받는 `--cleanup-only` 명령을 구현하고 `GENERATING` row가 남으면 cleanup `PENDING`으로 종료한다. `SETUP_IN_PROGRESS + exact anchor`, `CLEANUP_IN_PROGRESS + rows 존재/0건`은 idempotent saga로 복구한다. 이미 `RESOLVED`인 원장의 0건만 성공 no-op이며, 원장 누락·원장 identity 불일치·잘못된 UUID는 `FAIL`이다.

- [ ] **Step 8: 불명확한 결과와 복구 경로를 테스트한다**

  로그인과 처방 확정·가이드 생성·채팅 세션·메시지 전송 각각의 "서버 commit 후 응답 유실", "client timeout 동안 서버 계속 실행", "OpenAI·FastAPI 없이 cleanup-only", "이미 정리된 ledger run의 성공 no-op", "원장에 없는 유효 UUID", "원장 identity 불일치", "원래 image/schema 불일치 차단", "image ID/RepoDigest 혼동과 RepoDigest 누락", "resolver unknown run·변조 출력·read-only 권한·wrong-image 거부", "다른 run의 데이터 보존", "cleanup commit 후 사후 조회 잔존", "unresolved 원장으로 migration 차단", "normal과 migration의 advisory-lock 직렬화", "lock 획득 timeout·connection loss fail-closed"를 결정적으로 테스트한다. 각 응답 유실에서 exact ID 복구 후 남은 단계가 재개되고, 필수 단계 하나라도 미실행이면 전체 `PASS`가 아닌지 확인한다. fixture app commit 직후와 cleanup app commit·0건 검증 직후 process kill을 주입해 각각 `SETUP_IN_PROGRESS`, `CLEANUP_IN_PROGRESS`에서 복구되는지도 확인한다. reconciliation은 단계별 terminal 성공→해당 단계 `PASS`, terminal 실패→execution `FAIL`, 제한 시간 만료→execution `OUTCOME_UNKNOWN` 전이를 검사한다. normal의 execution·cleanup·overall 조합과 cleanup-only의 `execution=NOT_RUN`·독립 종료 코드를 모두 검사한다.

- [ ] **Step 9: 전용 테스트를 통과시킨다**

  Run: `uv run pytest app/tests/release_validation/test_ai_one_cycle_smoke.py -q`

  Expected: 실제 OpenAI 호출 없이 모든 테스트 PASS.

- [ ] **Step 10: Backend 회귀 검사를 실행한다**

  Run: `uv run ruff check app/release_validation app/tests/release_validation && uv run mypy app && uv run pytest app/tests/guide_ai app/tests/chat_ai app/tests/chat app/tests/chat_apis app/tests/repositories/test_guide_repository.py app/tests/repositories/test_chat_repository.py -q`

  Expected: 모두 PASS; live smoke 두 건은 명시적으로 SKIPPED.

### Task 3: 실패 상태 저장을 결정적 테스트 증거로 고정

**Files:**
- Modify only if a gap is found: `app/tests/guide_ai/test_backend_contract.py`
- Modify only if a gap is found: `app/tests/chat_apis/test_chat_message_api.py`
- Modify only if a gap is found: `app/tests/repositories/test_guide_repository.py`
- Modify only if a gap is found: `app/tests/repositories/test_chat_repository.py`

**Interfaces:**
- Consumes: 기존 오류 매핑과 repository commit 동작
- Produces: 실제 provider 장애 없이 재현 가능한 실패 영속화 증거

- [ ] **Step 1: 현재 실패 회귀 테스트를 실행한다**

  Run: `uv run pytest app/tests/guide_ai/test_backend_contract.py::test_backend_contract_maps_generation_errors_and_marks_failed app/tests/repositories/test_guide_repository.py::test_mark_failed_persists_after_subsequent_rollback app/tests/chat_apis/test_chat_message_api.py::test_failed_send_is_requeried_as_exact_user_and_failed_assistant_pair app/tests/repositories/test_chat_repository.py::test_commit_failed_message_pair_persists_exactly_one_user_failed_assistant_pair_after_rollback -q`

  Expected: 네 테스트 모두 PASS.

- [ ] **Step 2: 증거가 부족할 때만 회귀 assertion을 추가한다**

  Guide는 `FAILED`, 고정 `error_code/error_message`, non-null `completed_at`, null `content/model_name/prompt_version`; Chat은 연속된 USER와 FAILED ASSISTANT 한 쌍, ASSISTANT의 null `content/model_name/prompt_version`, 고정 오류 metadata를 모두 검증한다.

- [ ] **Step 3: 실제 OpenAI 장애 유도는 제외한다**

  잘못된 API key, rate limit 유도, 극단적으로 짧은 timeout을 live smoke에 사용하지 않는다. 이는 비용·계정 상태·네트워크에 의존하고 staging key를 불필요하게 위험에 노출한다.

### Task 4: staging 실행과 비밀·provenance 절차 연결

**Files:**
- Modify: `envs/example.local.env`
- Modify: `envs/example.prod.env`
- Create and implement Task 0 staging artifacts
- Modify: `docs/deployment.md`
- Modify: `app/Dockerfile`

**Interfaces:**
- Consumes: staging 전용 secret store의 `OPENAI_API_KEY`, baked commit SHA, running full RepoDigest·local image ID·OCI revision, authoritative run ledger
- Produces: exact staging Compose 경로를 사용하는 host wrapper와 Backend 통합 검증 기록

- [ ] **Step 1: 모든 예시 환경 파일을 명백한 placeholder로 정리한다**

  local·production 예시의 `SECRET_KEY`, DB password, OpenAI·CLOVA credential 형태 값을 실제 값으로 오인할 수 없는 placeholder로 교체한다. 실제 사용 이력은 비공개로 확인하고 사용된 값은 `SECURITY.md` 절차에 따라 회전한다.

- [ ] **Step 2: 이미지에 검증 모듈과 provenance를 포함한다**

  동일 image에 검증 모듈, build-time `DEPLOY_COMMIT_SHA`와 OCI revision label을 bake한다. wrapper가 inspect한 revision과 baked 값을 비교하고 pull 가능한 full RepoDigest를 immutable 실행 reference로 사용한다. local image ID는 보조 증거로만 기록하고 mutable tag나 image ID를 cleanup reference로 사용하지 않는다.

- [ ] **Step 3: host wrapper로 staging live smoke를 실행한다**

  wrapper는 `--project-name ah-staging`, `--project-directory infra/docker`, `--env-file envs/.staging.env`, `-f infra/docker/docker-compose.staging.yml`을 모두 명시한다. 실행 중 fastapi container의 full RepoDigest, local image ID와 OCI revision을 `docker inspect`로 읽어 각각 `RELEASE_VALIDATION_REPO_DIGEST`, `RELEASE_VALIDATION_LOCAL_IMAGE_ID`, `RELEASE_VALIDATION_IMAGE_REVISION`으로 operator-generated run ID와 함께 `release-validator` CLI에 전달한다. CLI가 lock 안에서 app DB write 전에 원장에 run ID, 세 provenance 값과 schema revision을 원자적으로 기록한다.

- [ ] **Step 4: 실패·중단 후 cleanup을 검증한다**

  고정된 `release-ledger-resolver` ops image에 run ID만 전달해 strict JSON provenance를 조회하고, wrapper가 contract version·run ID·full RepoDigest·revision을 검증한다. Unknown run, 추가·중복 출력, 형식 오류와 read-only 권한 위반은 cleanup image 시작 전에 차단한다. 검증된 값을 운영자 수기 전사 없이 다음 exact interface에 전달해 DB-only 복구를 검증한다.

  ```bash
  RELEASE_VALIDATION_IMAGE=<ledger-recorded-repository@sha256:repo-digest> \
  docker compose \
    --project-name ah-staging \
    --project-directory infra/docker \
    --env-file envs/.staging.env \
    -f infra/docker/docker-compose.staging.yml \
    run --rm --no-deps \
    -e RELEASE_CLEANUP_ALLOWED=1 \
    -e RELEASE_VALIDATION_RUN_ID=<same-operator-generated-uuid> \
    -e RELEASE_VALIDATION_REPO_DIGEST=<ledger-recorded-repository@sha256:repo-digest> \
    -e RELEASE_VALIDATION_IMAGE_REVISION=<ledger-recorded-oci-revision> \
    release-cleanup \
    uv run --no-sync python -m app.release_validation.ai_one_cycle_smoke --cleanup-only
  ```

  OpenAI Key와 FastAPI readiness가 없어도 cleanup을 허용한다. cleanup `PENDING`·`FAIL` 또는 잔존 row가 있으면 Backend 검증을 완료하지 않는다.

- [ ] **Step 5: Backend 통합 증거를 기록한다**

  환경, commit SHA, immutable full RepoDigest·local image ID·OCI revision, run ID, 실행 시각·실행자, 모델 ID, 두 prompt version, execution·cleanup·overall 결과만 기록한다. key, token, 질문·응답 전문은 기록하지 않는다. 이 기록은 Production 배포 승인과 분리한다.

### Task 5: Frontend owner에게 별도 E2E 계획 인계

현재 저장소에는 Playwright dependency·configuration·표준 spec 형식이 없으며 실제 가이드·챗봇 route도 연결되지 않았다. 따라서 이 Backend 계획에서 브라우저 E2E 구현이나 완료를 주장하지 않는다.

- [ ] Frontend owner가 화면 구현 범위와 E2E Issue를 별도로 생성한다.
- [ ] 사용할 브라우저 도구, dependency·lockfile·configuration과 실행 명령을 확정한다.
- [ ] 합성 fixture 준비·삭제 책임과 Backend smoke 데이터 미재사용 원칙을 기록한다.
- [ ] screenshot·trace·console artifact에서 token과 생성 본문을 제거하는 정책을 정의한다.
- [ ] Frontend E2E 결과와 Backend 통합 검증 결과를 각각 독립 상태로 보고한다.

### Task 6: 저장소 완료·전달 Gate 통과

- [ ] **Step 1: 저장소 필수 검사를 실행한다**

  Run: `uv run ruff check . && uv run ruff format . --check && uv run mypy app ai_worker && bash scripts/ci/run_test.sh`

  Expected: 모두 PASS. PostgreSQL·Docker 등 선행 조건을 충족하지 못한 검사는 생략하지 않고 blocker로 기록한다.

- [ ] **Step 2: staging 전용 artifact를 검증한다**

  staging Compose render, service별 secret allowlist, `release-validator` network namespace, pinned resolver image, ledger schema·role 권한·append-only event·retention, normal·cleanup·migration lock과 fault-injection test를 실행한다. 승인된 staging에서만 실제 OpenAI one-cycle을 한 번 실행하고 결과·cleanup `PASS`를 기록한다. 이 결과를 Production 승인으로 사용하지 않는다.

- [ ] **Step 3: 최종 문서·보안·diff를 검토한다**

  변경된 Markdown 링크·상태 표기와 `docs/contracts/` index를 검사한다. credential pattern과 실제 환자·처방 데이터가 diff에 없는지 확인하고 `git diff --check`, complete `git diff`, `git status --short`를 검토한다. 신규 파일이 untracked로 남아 있지 않도록 commit 대상 전체를 확인한다.

- [ ] **Step 4: Issue branch에서 commit·push하고 PR을 생성한다**

  `CONTRIBUTING.md`의 Issue 번호와 branch·commit 형식을 사용한다. `develop`에 직접 push하지 않고 작업 branch를 push해 `develop` 대상 PR을 만든다. PR 본문에는 Backend smoke, 기능 완료, Production 승인 상태를 분리하고 실행·미실행 검사를 모두 기록한다.

- [ ] **Step 5: CODEOWNER 리뷰와 CI를 통과한다**

  최소 Backend `@phina-io`, 배포·아키텍처 `@hazelnutflavoured`, 기본·AI `@ceohwj`의 승인을 받는다. Frontend 파일이나 E2E 계약을 변경한 경우 `@solia142`도 요청한다. 모든 필수 CI가 PASS인지 확인하고 실패·skip을 승인으로 간주하지 않는다.

- [ ] **Step 6: squash merge와 branch 정리를 완료한다**

  승인과 CI 통과 후 repository ruleset에 따라 squash merge하고 원격·로컬 작업 branch를 정리한다. 관련 Issue에는 merge된 PR과 최종 검증 증거를 연결한다.

## Self-Review Result

- 요구사항 매핑: staging 격리(Task 0), 실제 OpenAI smoke(Task 4), 처방 확정부터 챗봇 전체 흐름(Task 2), 모델·프롬프트 저장(Task 2), 실패 저장(Task 3), Frontend 별도 인계(Task 5), FastAPI 전용 key(Task 0·4).
- 계약 변경: 기존 사용자 API·application DB 의미는 유지하지만 staging control DB ledger와 validation·cleanup·migration role 사이에 새 proposed 운영 계약을 추가한다. `docs/contracts/proposed/operations/release-validation-ledger.md`, index, schema·구현과 contract/fault-injection test를 같은 구현 PR에서 갱신한다.
- 주요 위험: `app/tests/conftest.py`는 test DB schema를 생성·삭제하므로 기존 `app/tests/**/test_smoke.py`를 배포 컨테이너의 운영 DB 설정으로 직접 실행하지 않는다.
- 완료 구분: Task 0~4는 Backend 통합 검증 구현, Task 5는 Frontend 별도 인계, Task 6은 저장소 전달을 완료한다. 기능 Issue와 Production 배포 승인은 별도 authoritative gate에서 판정한다.
