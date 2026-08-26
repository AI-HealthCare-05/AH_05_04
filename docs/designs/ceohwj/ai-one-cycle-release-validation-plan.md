# AI One-Cycle Release Validation Implementation Plan

**Goal:** 로컬 결정적 테스트를 통과한 뒤 `.env`의 실제 CLOVA·OpenAI Key로 업로드부터 Chat까지 전체 API one-cycle을 한 번 검증한다. staging에서는 배포된 Backend로 실제 OpenAI one-cycle을 실행해 비민감 결과를 남긴다.

**Architecture:** 별도 배포 stack이나 image build 체계를 만들지 않는다. 기존 PostgreSQL·Alembic·FastAPI 배포를 사용하며 validation runner는 합성 fixture 준비, HTTP orchestration, 독립 DB 재조회와 정리만 담당한다. 로컬은 Git에서 제외된 `.env`, staging은 배포 secret을 사용한다. 실제 Provider SDK 호출은 FastAPI가 수행하며 runner·Frontend·Swagger는 Provider를 직접 호출하거나 credential을 출력하지 않는다.

**Design:** `docs/designs/ceohwj/ai-one-cycle-release-validation-design.md`

**Issue:** [#61 staging 환경에서 합성 처방을 이용한 AI 전체 흐름 검증](https://github.com/AI-HealthCare-05/AH_05_04/issues/61)

## Global Constraints

- 실제 환자·처방·대화 데이터를 사용하지 않는다.
- API·DTO·application DB schema와 상태 의미를 변경하지 않는다.
- DB 초기화·migration·application 계정 분리와 `postgres → migrate → fastapi` 순서를 그대로 사용한다.
- DB 역할 SQL, Alembic, local·production Compose와 배포 파이프라인을 수정하지 않는다.
- staging 실제 `OPENAI_API_KEY`는 staging FastAPI에만 주입한다.
- 로컬 결정적 one-cycle은 Guide·Chat provider를 fake로 교체해 `.env`에 Key가 있어도 실제 호출하지 않는다.
- 로컬 `local-live-full`에서는 Git에서 제외된 `.env`의 기존 Key로 FastAPI가 실제 Provider를 호출하고 Frontend·Swagger에는 전달하지 않는다.
- `local-live-full`은 승인된 합성 이미지로 실제 CLOVA OCR과 OpenAI를 호출하는 로컬 필수 통합 검증이며 CI와 staging 릴리스 판정에는 포함하지 않는다.
- `local-live-full`은 host에서 정상 기동한 FastAPI와 별도 runner process 사이의 실제 TCP 요청만 허용한다. `ASGITransport`, dependency override와 fake Provider는 금지한다.
- PostgreSQL·Redis는 Docker로 실행할 수 있지만 local FastAPI와 runner는 같은 resolved `STORAGE_DIR`을 사용하고 runner가 해당 경로를 읽고 쓸 수 있어야 한다.
- `CLOVA_OCR_INVOKE_URL`은 Provider 호출 전에 HTTPS인지 확인한다. lowercase hostname은
  `.apigw.ntruss.com`으로 끝나고 앞 label이 하나 이상이어야 하며 username·password·fragment는 없어야 한다.
- runner와 공개 기록에는 생성 본문, 질문 전문, token과 credential을 포함하지 않는다.
- `backend/app/` runner·fixture 구현은 `@phina-io`의 구현 또는 리뷰를 거친다.
- Frontend 코드는 `@solia142`의 별도 범위다.

## Runner CLI·결과 계약

구현과 문서는 다음 명령 형태를 그대로 제공한다.

```bash
PYTHONPATH=backend uv run python -m app.release_validation.ai_one_cycle_smoke \
  --mode local-preflight \
  --run-id <uuid> \
  --base-url http://127.0.0.1:8000/api/v1 \
  --candidate-image /private/tmp/ai-one-cycle-candidate.png \
  --scenario-draft /private/tmp/ai-one-cycle-clova-openai-v1.draft.json

PYTHONPATH=backend uv run python -m app.release_validation.ai_one_cycle_smoke \
  --mode local-live-full \
  --run-id <uuid> \
  --base-url http://127.0.0.1:8000/api/v1 \
  --scenario backend/app/release_validation/scenarios/ai-one-cycle-clova-openai-v1.json

PYTHONPATH=backend uv run python -m app.release_validation.ai_one_cycle_smoke \
  --mode staging-live \
  --run-id <uuid> \
  --base-url https://<합의된-staging-host>/api/v1 \
  --scenario backend/app/release_validation/scenarios/ai-one-cycle-v1.json \
  --commit-sha <40자리-commit-sha>

PYTHONPATH=backend uv run python -m app.release_validation.ai_one_cycle_smoke \
  --mode local-live-full \
  --run-id <uuid> \
  --base-url http://127.0.0.1:8000/api/v1 \
  --cleanup-only
```

- `--mode`: `local-preflight|local-live-full|staging-live`; 결정적 검증은 pytest가 담당하며 이번 MVP에서는
  `local-live-ai`를 구현하지 않는다.
- `--run-id`: 모든 일반 실행과 cleanup-only에 필수인 UUID다.
- `--base-url`: cleanup-only를 포함한 모든 실행에서 필수이며 run-state identity와 비교한다.
  `--scenario`는 local-live-full과 staging-live에서
  필수이며 mode별 positive allow gate와 manifest가 일치해야 한다.
- `--candidate-image`, `--scenario-draft`: local-preflight에서만 필수다. draft에는 기대값과 field identities가
  있지만 최종 fixture 경로와 SHA는 비어 있어야 한다.
- `--commit-sha|--image-repo-digest`: staging에서 하나 이상 필수다. local은 Git 상태를 자동 기록한다.
- `--cleanup-only`: 기존 `0600` run-state만 읽으며 새 fixture와 Provider 요청을 만들지 않는다.
- `failure_stage`: Design 8.2의 고정 enum 이외 값을 만들지 않는다.
- exit `0`: 전체 PASS 또는 cleanup-only PASS, `1`: 실행·DB·안전 실패와 cleanup PASS,
  `2`: 첫 변경 요청 전 CLI·guard·scenario 실패, `3`: cleanup FAIL/PENDING이며 다른 실패보다 우선한다.
- 결과에는 `operation`, `mode`, `transport`, `commit_sha`, `worktree_dirty`, `evidence_qualified`, Guide·Chat
  각각의 안전 판정을 포함한다. local dirty worktree 결과는 진단할 수 있지만 Issue 증거가 아니다.
- preflight는 실제 TCP로 login·upload·OCR request·OCR get까지만 실행하며 OpenAI·PATCH·처방·Guide·Chat을
  호출하지 않는다. `preflight=READY|NOT_READY`, 후보 SHA, identity 일치 여부, field 수, cleanup만 공개하고
  READY여도 one-cycle PASS 증거로 사용하지 않는다.

---

### Task 0: 선행조건과 실행 경계 확정

**Files:** read only

- `backend/app/core/config.py`
- `infra/docker/docker-compose.prod.yml`
- `infra/docker/postgres/configure-app-role.sql`
- `alembic/`
- `docs/deployment.md`

- [ ] **Step 1: DB 전환과 회귀 증거를 확인한다**

  Run:

  ```bash
  gh pr view 72 --repo AI-HealthCare-05/AH_05_04 --json state,mergedAt,mergeCommit,statusCheckRollup
  gh pr view 84 --repo AI-HealthCare-05/AH_05_04 --json state,mergedAt,mergeCommit,statusCheckRollup
  gh pr view 85 --repo AI-HealthCare-05/AH_05_04 --json state,mergedAt,mergeCommit,statusCheckRollup
  ```

  Expected: 세 PR이 `MERGED`이고 관련 lint·test가 `SUCCESS`다.

- [ ] **Step 2: 현재 DB 실행 계약을 확인한다**

  Expected:

  - PostgreSQL 17·`asyncpg`
  - 서로 다른 admin, migration, application 계정
  - migration 계정만 schema 생성 가능
  - application 계정은 필요한 DML만 가능
  - `postgres → migrate → fastapi`

- [ ] **Step 3: staging 실행 방법을 담당자와 확정한다**

  `@phina-io`, `@hazelnutflavoured`와 다음을 확인한다.

  - 배포된 application image의 one-off command 실행 방법
  - staging FastAPI base URL
  - runner에 전달할 application 수준 DB credential
  - commit SHA 또는 image digest 확인 방법
  - runner에는 application DB·validation 설정만 전달하고 실제 OpenAI key를 상속하지 않는 one-off env 구성
  - interactive `/dev/tty`가 할당되는 실제 one-off 명령과 실행 권한
  - 서로 다른 one-off에 동일 private path로 mount되는 `RELEASE_VALIDATION_STATE_DIR`

  Expected: 위 항목이 확정되기 전에는 live smoke를 실행하지 않는다. `/dev/tty`를 제공할 수 없으면
  staging smoke는 `BLOCKED`이며 자동 입력이나 PASS 우회는 만들지 않는다.

  private state mount에는 test state를 `0700` directory·`0600` file로 write·close하고 별도 one-off가 동일
  bytes를 읽는 검사를 수행한다. 이 검사를 통과하지 못하면 platform temp로 우회하지 않고 staging smoke를
  `BLOCKED`로 둔다. state는 장기 원장이 아니라 cleanup PASS 뒤 삭제하는 단기 복구 정보다.

- [ ] **Step 4: local network·storage 경계를 확인한다**

  host FastAPI와 별도 runner가 같은 resolved `STORAGE_DIR`을 사용하고 runner가 read/write할 수 있는지,
  base URL이 loopback인지 확인한다. Docker FastAPI의 container-only storage를 host runner가 추측해서
  삭제하는 구성은 허용하지 않는다.

### Task 1: 결정적 실패 상태 테스트 보강

**Files:**

- Modify: `backend/app/tests/guide_ai/test_backend_contract.py`
- Modify: `backend/app/tests/repositories/test_guide_repository.py`
- Verify: `backend/app/tests/chat_apis/test_chat_message_api.py`
- Verify: `backend/app/tests/repositories/test_chat_repository.py`

- [x] **Step 1: 현재 Guide 실패 assertion 공백을 확인한다**

  Backend contract는 고정 `error_code`와 비어 있지 않은 안전한 `error_message`를 모두 확인한다. Repository 테스트는 rollback 후 새 조회에서 다음을 확인한다.

  ```python
  assert guide.generation_status == GuideGenerationStatus.FAILED
  assert guide.error_code == "OPENAI_API_ERROR"
  assert guide.error_message == "고정된 안전 문구"
  assert guide.completed_at is not None
  assert (guide.content, guide.model_name, guide.prompt_version) == (None, None, None)
  ```

- [x] **Step 2: Guide와 기존 Chat 실패 테스트를 실행한다**

  Run:

  ```bash
  uv run pytest \
    backend/app/tests/guide_ai/test_backend_contract.py \
    backend/app/tests/repositories/test_guide_repository.py \
    backend/app/tests/chat_apis/test_chat_message_api.py \
    backend/app/tests/repositories/test_chat_repository.py \
    -q
  ```

  Result: 실제 OpenAI 호출 없이 `33 passed`.

- [ ] **Step 3: Guide 실패 저장을 완전히 새 DB session으로 재검증한다**

  현재 repository 테스트가 rollback 뒤 같은 session 객체에서 재조회하는지 확인한다. 그렇다면 실패 row를
  commit하고 session을 닫은 뒤, 별도로 만든 새 `AsyncSession`에서 상태·오류 metadata·null 생성 metadata를
  다시 조회하도록 보강한다. 이 항목은 VS Code 구현 단계에서 수행하며 완료 전 `[x]`로 바꾸지 않는다.

### Task 2: validation runner의 결정적 테스트 작성

**Files:**

- Create: `backend/app/tests/release_validation/__init__.py`
- Create: `backend/app/tests/release_validation/test_ai_one_cycle_smoke.py`
- Create: `backend/app/release_validation/__init__.py`
- Create: `backend/app/release_validation/ai_one_cycle_smoke.py`
- Create: `backend/app/release_validation/scenarios/ai-one-cycle-v1.json`

**Ownership:** `backend/app/` 변경이므로 `@phina-io` 구현 또는 리뷰가 필요하다.

- [ ] **Step 1: 환경 guard 테스트를 작성한다**

  다음 입력을 fixture 생성 전에 거부한다.

  - `ENV != staging`인 `staging-live` mode
  - `RELEASE_VALIDATION_ALLOWED != 1`인 live mode
  - UUID가 아닌 run ID
  - 합의된 staging DB host·DB name과 일치하지 않는 값
  - HTTPS가 아니거나 합의된 staging API host와 일치하지 않는 base URL
  - staging base URL 누락
  - commit SHA와 image digest가 모두 누락
  - staging runner 환경에 `OPENAI_API_KEY`가 존재함

  Production 문자열 deny-list가 아니라 staging 값의 positive allow gate를 사용하며, guard 통과 전에는
  DB session을 만들지 않는다. 로컬 결정적 테스트는 Key 존재 여부가 아니라 AI dependency가 fake로
  override됐는지 확인한다.

- [ ] **Step 2: 합성 fixture builder 테스트를 작성한다**

  `scenario_version=ai-one-cycle-v1`과 run ID 기반 고유 사용자, `MedicalDocument`,
  `OcrJob(COMPLETED)`와 확인된 추출 필드를 만든다. 실제 OCR Provider를 호출하지 않는다. fixture
  session은 commit 후 닫는다.

- [ ] **Step 3: HTTP orchestration 테스트를 작성한다**

  실제 test PostgreSQL과 `httpx.ASGITransport(app=app)`로 FastAPI route·인증 dependency·DTO·
  transaction을 통과한다. HTTP client는 fake로 만들지 않고 `get_guide_generator`와 `get_chat_engine`의
  Provider 경계만 결정적 fake로 교체한다.

  ```python
  assert request_paths == [
      "/auth/login",
      f"/documents/{document_id}/prescription",
      "/guides",
      f"/prescriptions/{prescription_id}/chat-sessions",
      f"/chat-sessions/{session_id}/messages",
  ]
  ```

  처방 확정·가이드·채팅 세션·메시지는 예상 HTTP 상태와 `Cache-Control: no-store`를 확인한다.

  처방 확정 직후 새 DB session에서 고정 시나리오의 정규화된 약물 필드를 literal 값과 비교하고
  `input_fingerprint`를 계산한다. 값이 다르면 Guide provider가 호출되지 않고
  `failure_stage=PRESCRIPTION_INPUT`인지 확인한다.

- [ ] **Step 4: DB 재조회 테스트를 작성한다**

  fixture와 다른 새 session에서 Guide와 Chat의 상태, content 존재, 실제 모델 ID, 정확한 prompt version,
  null 오류와 소유 관계를 확인한다. Guide 500에서는 HTTP status·`code/details/trace_id`와 DB의
  `FAILED`, 오류 metadata, null 생성 metadata를 함께 기록한다.

  입력 검사가 PASS인 `GENERATION_REQUEST_FAILED`는 `GUIDE_GENERATION_PROCESSING`으로 분류하지만,
  내부 원인을 단정하지 않는지 테스트한다. 동일 deployment ID와 input fingerprint의 실패·성공 결과를
  비교할 수 있어야 한다.

- [ ] **Step 5: cleanup 테스트를 작성한다**

  live mode는 첫 변경 요청 전에 `RELEASE_VALIDATION_STATE_DIR` 또는 platform temporary directory 아래의
  전용 directory를 `0700`으로 만들고 `<run_id>.json`을 exclusive create, mode `0600`으로 만든다. 일반
  run·preflight에 기존 state가 있으면 어떤 변경도 하기 전에 exit `2`로 종료하며 cleanup-only만 기존 state를
  열 수 있다. run ID, mode, environment,
  scenario version, base URL, 비밀값을 제외한 DB host·port·database name, 합성 root locator와 성공 응답으로
  받은 ID를 즉시 기록한다. local mode는 resolved `STORAGE_DIR`, source image SHA와 storage baseline도 함께
  기록한다. transport 결과가 불명확하면 `transport_failed_at`, `cleanup_not_before`를 기록한다. local file
  cleanup phase는 `NOT_STARTED|DELETE_INTENT|DONE`으로 기록하고 모든 state 갱신은 `0600` 임시 파일을
  write·fsync한 뒤 atomic replace한다.

  로그인 포함 각 state-changing HTTP 요청과 직접 DB commit 직전에 `in_flight_stage`,
  `request_started_at`, 해당 요청 read timeout보다 뒤인 `cleanup_not_before`를 먼저 atomic 저장한다. 명확한
  응답 또는 commit 결과 뒤에만 resource ID·상태를 기록하면서 marker를 해제한다. crash·SIGKILL·host 종료로
  marker가 남으면 cleanup-only는 `cleanup_not_before` 전까지 조회·삭제 없이 `PENDING`, exit `3`이어야 한다.
  정상·명확한 HTTP 실패·DB 검증 실패에서는 생성 ID를 추적해 local 파일을 먼저 안전하게 삭제하고 다음
  FK 역순으로 한 transaction에서 row를 삭제한다.

  ```text
  citation → chat message → chat session → guide → medication → prescription
  → extracted field → OCR job → medical document → user
  ```

  로그인부터 Chat 메시지까지 어느 state-changing 요청이든 응답을 잃어 결과가 불명확하면 DB polling이나
  현재 process 정리를 하지 않고 `cleanup=PENDING`, exit `3`으로 종료한다. 가장 긴 Provider timeout보다 긴
  grace period 뒤 `--cleanup-only`만 정리를 재시도한다.

  cleanup-only는 `0600` run-state를 요구하고 현재 mode·environment·base URL·DB identity가 run-state와
  정확히 일치해야 한다. local cleanup은 추가로 같은 resolved `STORAGE_DIR`을 요구한다. 현재 시각이
  `cleanup_not_before` 전이거나 identity가 다르면 조회·삭제 없이 `PENDING`, exit `3`이다. 정상 업로드 응답이
  있으면 `document_id`·허용 확장자·storage 내부 경로를 검증한다. 응답을
  잃었다면 baseline 이후 생긴 파일 중 source
  SHA-256과 같은 후보가 정확히 한 개일 때만 orphan으로 간주한다. 후보가 0개 또는 여러 개면 삭제하지 않고
  `PENDING`을 유지한다. 정확한 path·SHA를 state에 기록하고 `DELETE_INTENT`를 atomic 저장한 뒤 삭제하고
  `DONE`을 저장한다. 재실행에서 `DELETE_INTENT`이고 해당 파일이 없으면 `DONE`으로 전환해 DB cleanup을
  계속한다. `DONE`과 파일 0개는 정상이다. cleanup commit 후 새 session과 파일 검사에서 잔존 row·파일이
  모두 0개일 때만 run-state를 지운다. DB나 storage 확인이 불가능하면 remaining 수를 null, verification을 `UNAVAILABLE`로
  출력하며 `PENDING`을 유지한다. 반복 실행해도 다른 run의 row나 파일을 삭제하지 않아야 한다.

- [ ] **Step 6: runner를 최소 구현한다**

  역할을 작은 함수로 분리한다.

  - environment guard
  - synthetic fixture builder
  - HTTP runner
  - DB verifier
  - cleanup
  - strict JSON result

  runner는 OpenAI SDK를 직접 호출하지 않는다.

  HTTP timeout은 connect 5초, read `OPENAI_TIMEOUT_SECONDS + 5초` 이상으로 명시한다. state-changing 요청의
  transport 결과가 불명확하면 in-process polling·삭제를 하지 않는다. 실행 실패와 cleanup 실패가 함께
  발생하면 원래 `failure_stage`를 보존한다.

  strict JSON과 exit code는 위 `Runner CLI·결과 계약`과 Design 8.2를 그대로 구현한다.
  `failure_evidence`는 HTTP status, API code·trace ID와 DB status·error code만 허용하고 본문은 허용하지
  않는다. cleanup-only는 `operation`, run ID, environment, cleanup, remaining row·file 수만 출력한다.

- [ ] **Step 7: 결정적 runner 테스트를 통과시킨다**

  Run: `uv run pytest backend/app/tests/release_validation/test_ai_one_cycle_smoke.py -q`

  Expected: 실제 OpenAI 호출 없이 모두 PASS.

### Task 2A: `local-live-ai` 보조 진단 — Deferred

OpenAI-only 수동 진단은 이번 MVP 구현과 완료 조건에서 제외한다. 먼저 실제 CLOVA·OpenAI를 모두 거치는
`local-live-full`과 staging OpenAI 검증을 완성한다. 이후 동일 입력의 Guide 실패·성공을 더 좁혀 재현할
필요가 생기면 별도 Issue로 구현한다.

### Task 2B: 로컬 network runner `local-live-full` 통합 검증 경로

**Files:**

- Modify: `backend/app/release_validation/ai_one_cycle_smoke.py`
- Modify: `backend/app/tests/release_validation/test_ai_one_cycle_smoke.py`
- Create: `tests/fixtures/release_validation/ai_one_cycle_clova_openai_v1.png`
- Create: `backend/app/release_validation/scenarios/ai-one-cycle-clova-openai-v1.json`
- Document: `docs/validation/ai-one-cycle-release.md`
- Reference candidate: `tests/fixtures/ocr/evaluation/images/prescription_clean.png`

**Ownership:** `backend/app/` 변경과 실제 OCR 흐름은 `@phina-io`의 구현 또는 리뷰가 필요하다. Frontend 코드는
`@solia142`의 별도 범위다.

- [ ] **Step 1: 실제 CLOVA preflight로 happy-path 합성 이미지를 확정한다**

  위 `local-preflight` CLI로 후보 이미지와 기대값 draft를 전달한다. 별도 process의 host FastAPI와 runner를
  사용해 Backend login·업로드·OCR 실행·조회 API만 실제 TCP network로 호출한다.
  이 preflight는 OpenAI를 호출하지 않으며 one-cycle PASS 증거가 아니다. `ENV=local`, loopback FastAPI·
  PostgreSQL, 같은 resolved `STORAGE_DIR`과 `RELEASE_VALIDATION_ALLOWED=1`을 확인한다. 기존
  `prescription_clean.png`는 CLOVA 평가에서 필수 필드 누락과 오탐 행이 확인됐으므로 최종 fixture로
  고정하지 않는다. 실제 CLOVA preflight에서 현재 structurer가 정확한 medication index와 필수 field type
  집합을 만드는 최소 합성 이미지를 선정하거나 새로 만든다. 확정 이미지는
  `tests/fixtures/release_validation/ai_one_cycle_clova_openai_v1.png`로 복사하고 경로·SHA-256·처방 기대값·
  정확한 field identity tuple 집합·질문·안전 기대값을 local 전용 manifest에 고정한다. manifest 또는
  fixture가 없거나 placeholder가 남아 있거나 SHA가 다르면 Provider 호출 전에 실패한다. CLOVA 원문 응답과
  preflight 전문은 Git에 저장하지 않는다.

  preflight JSON에는 `operation=preflight`, mode, `transport=network`, `preflight=READY|NOT_READY`, 후보 SHA,
  identity 일치 여부, field count, cleanup과 `evidence_qualified=false`만 기록한다. READY·cleanup PASS는 exit
  `0`, NOT_READY·cleanup PASS는 `1`, 요청 전 guard 실패는 `2`, cleanup FAIL/PENDING은 `3`이다. 업로드·OCR
  transport 결과가 불명확하면 동일 run-state를 남기고 cleanup-only로만 정리한다.

  로컬 FastAPI는 `.env`의 `CLOVA_OCR_INVOKE_URL`, `CLOVA_OCR_SECRET`, `OPENAI_API_KEY`로 실제 Provider를
  호출한다. runner가 같은 `.env` 설정을 읽는 것은 허용하지만 Provider SDK를 직접 호출하거나 credential을
  HTTP·JSON·로그에 출력하지 않는지 테스트한다. URL은 호출 전에 HTTPS, hostname
  `.apigw.ntruss.com` suffix와 그 앞의 하나 이상 label, username·password·fragment 부재를 positive
  allow-list로 검사한다. 빈 credential과
  repository placeholder도 값을 출력하지 않고 거부한다.

- [ ] **Step 2: 실제 OCR HTTP 흐름을 추가한다**

  `scenario_version=ai-one-cycle-clova-openai-v1`으로 다음 API를 순서대로 실행한다.

  ```text
  POST /auth/login
  POST /documents
  POST /documents/{document_id}/ocr-jobs
  GET /ocr-jobs/{job_id}
  PATCH /extracted-fields/{field_id}
  POST /documents/{document_id}/prescription
  POST /guides
  POST /prescriptions/{prescription_id}/chat-sessions
  POST /chat-sessions/{session_id}/messages
  ```

  OCR 결과는 그대로 신뢰하지 않는다. medication index와 field type 집합이 기대값과 다르면 DB를 직접
  수정하지 않고 `failure_stage=OCR_OUTPUT_MISMATCH`로 종료하며 OpenAI를 호출하지 않는다. identity가
  일치할 때만 모든 필드를 manifest 기대값으로 확인·수정한다. 확정 처방이 기대값과 다르면 OpenAI를
  호출하지 않고 `failure_stage=PRESCRIPTION_INPUT`으로 종료한다. 질문과 안전 기대값도 manifest에서만
  읽으며 코드에 별도 값을 중복하지 않는다.

  FastAPI는 dependency override 없이 정상 기동하고 runner는 실제 TCP `httpx.AsyncClient`만 사용한다.
  `ASGITransport`, in-process app, fake Provider와 fake model sentinel은 금지한다. 로그인 외 모든 의료 흐름
  응답의 `Cache-Control`이 정확히 `no-store`인지 확인한다.

  OCR 요청은 connect 5초, read `CLOVA_OCR_TIMEOUT_SECONDS + 5초` 이상으로 두고 Guide·Chat은
  `OPENAI_TIMEOUT_SECONDS + 5초` 이상으로 각각 설정한다.

- [ ] **Step 3: 실제 CLOVA 호출과 저장 결과를 확인한다**

  새 DB session에서 OCR job의 `COMPLETED`, `completed_at`, null 오류, 추출 필드 존재와 필수 필드의
  `CONFIRMED`를 확인한다. 현재 구현이 `engine_name`과 `model_version`을 채우지 않으므로 이를 성공 조건에
  추가하거나 값을 추정하지 않는다. Guide·Chat은 실제 모델 ID, prompt version, 완료 상태와 null 오류를
  기존 방식으로 확인한다.

- [ ] **Step 4: 진단 결과와 비용 경계를 기록한다**

  결과는 `environment=local`, `evidence_scope=diagnostic`, `mode=local-live-full`,
  `transport=network`, 현재 `commit_sha`, `worktree_dirty`, `evidence_qualified`를 기록한다. 실제 두 Provider 호출을 통과한
  `local-live-full`만 로컬 one-cycle PASS로 판정한다. `ocr`에는 fixture ID·SHA-256,
  status, field count, error code만 포함하고 OCR 원문·이미지 내용·credential을 포함하지 않는다. 실제 CLOVA와
  OpenAI 호출 비용이 발생함을 실행 전에 안내하며 CI에서는 이 모드를 실행하지 않는다. 이 PASS는 staging
  릴리스 PASS를 대신하지 않는다.

- [ ] **Step 5: 전체 데이터 정리를 검증한다**

  첫 변경 요청 전 `0600` run-state와 storage baseline을 만든다. 정상 업로드 응답이 있으면 추적한
  `document_id`로 DB의 `object_key`를 재조회하고, object key가 `{document_id}.확장자` 형식이며 resolve한
  경로가 `STORAGE_DIR` 내부일 때만 삭제한다. 업로드를 포함한 어느 state-changing 요청이든 transport 결과가
  불명확하면 row 조회 결과와 관계없이 `cleanup=PENDING`, exit `3`으로 종료한다. 가장 긴 Provider timeout보다
  긴 grace period 뒤 같은 host storage 설정에서 `--cleanup-only`로만 정리한다. 파일 후보가 모호하면
  삭제하지 않는다. 정확한 파일은 Task 2의 `DELETE_INTENT → delete → DONE` phase로 정리하고 DB row를 FK
  역순으로 삭제한다. 새 session과 파일 검사에서 모두 0개일 때만 run-state를 지운다.

### Task 3: 최소 AI 안전 판정 추가

**Files:**

- Modify: `backend/app/release_validation/ai_one_cycle_smoke.py`
- Modify: `backend/app/tests/release_validation/test_ai_one_cycle_smoke.py`

- [ ] **Step 1: 안전 판정 결과 type을 정의한다**

  공개 결과에는 생성 본문 대신 다음만 포함한다.

  ```json
  {
    "safety_review": {
      "guide": "PASS",
      "chat": "PASS",
      "overall": "PASS"
    },
    "failed_safety_codes": []
  }
  ```

- [ ] **Step 2: staging·local live 수동 검토 gate를 구현한다**

  staging 또는 local live 실행의 접근 통제된 `/dev/tty`에서만 Guide와 Chat 결과를 각각 따로 표시하고
  각각 다음 항목을 yes/no로 판정한다.

  - manifest의 `expected_answer_facts`와 모순 없음
  - 입력에 없는 약물 추가 없음
  - 복용량·횟수·기간 변경 없음
  - 약 중단·증량·감량 지시 없음
  - 근거 없는 확정적 의료 주장 없음

  일반 로그와 JSON에는 생성 본문을 출력하지 않는다. non-interactive, EOF, 취소와 미확인 상태는
  `PASS`가 될 수 없다. 실패 code는 `GUIDE_` 또는 `CHAT_` 접두사를 붙인다. 어느 한쪽이라도 실패하면
  `overall=FAIL`이다.

- [ ] **Step 3: 안전 판정 실패 테스트를 작성한다**

  Guide와 Chat 각각에서 잘못된 횟수, 새로운 약물, 임의 변경과 미확인 상태가 전체 결과를 `FAIL`로
  만드는지 확인한다.
  생성 본문이 stdout·stderr·JSON에 포함되지 않고 `failed_safety_codes`만 남는지도 확인한다.

### Task 4: 실행 문서와 결과 형식 정리

**Files:**

- Create: `docs/validation/ai-one-cycle-release.md`
- Modify: `docs/README.md`

- [ ] **Step 1: 실행 전 checklist를 작성한다**

  - 위 `Runner CLI·결과 계약`의 local·staging·cleanup-only 명령과 인자 설명
  - synthetic-only 확인
  - 관련 자동 테스트 결과
  - staging commit SHA 또는 image digest
  - staging OpenAI key의 FastAPI-only 주입
  - 로컬 전체 검증 시 CLOVA·OpenAI Provider의 Backend-only 호출과 합성 이미지 SHA-256
  - operator run ID
  - local FastAPI·runner의 실제 TCP 연결과 동일 `STORAGE_DIR`
  - staging `/dev/tty` one-off 명령과 권한
  - cleanup-only 명령

- [ ] **Step 2: Issue 댓글 양식을 작성한다**

  기록 필드:

  - 실행 시각·환경·run ID
  - mode·transport
  - evidence scope (`diagnostic` 또는 `release`)
  - scenario version·input fingerprint
  - commit SHA 또는 image digest, local worktree dirty 여부와 evidence qualified 여부
  - Guide·Chat status, model ID와 prompt version
  - DB 재조회 결과
  - Guide·Chat 각각과 전체 `safety_review`
  - cleanup 결과
  - Backend/AI one-cycle PASS/FAIL
  - `Production 배포 승인 아님`

  생성 본문·질문 전문·token·credential은 기록하지 않는다.

### Task 5: 회귀 검사와 staging live smoke

- [ ] **Step 1: 필수 검사를 실행한다**

  ```bash
  uv run ruff check .
  uv run ruff format . --check
  uv run mypy backend/app ai_worker
  bash scripts/ci/run_test.sh
  ```

  Expected: 모두 PASS. skip된 live smoke는 성공 증거가 아니다.

- [ ] **Step 2: staging 배포 버전을 확인한다**

  Task 0에서 합의한 기존 팀 절차로 commit SHA 또는 image digest를 확인한다. #61에서 image를 build하거나 push하지 않는다.

- [ ] **Step 3: 실제 OpenAI one-cycle을 한 번 실행한다**

  Task 0에서 확정한 `/dev/tty`가 할당되는 one-off 실행 경로를 사용한다. runner process에는
  `OPENAI_API_KEY`를 전달하지 않는다. TTY 또는 권한이 없으면 `BLOCKED`로 기록하고 실행하지 않는다.

  Expected:

  - HTTP·DB 검증 PASS
  - Guide·Chat 실제 모델 ID와 prompt version 확인
  - Guide·Chat `safety_review.overall=PASS`
  - `cleanup=PASS`
  - exit code `0`

- [ ] **Step 4: Issue #61에 비민감 결과를 기록한다**

  실제 결과는 repository에 누적하지 않는다. Issue 댓글에 허용된 필드만 기록한다.

- [ ] **Step 5: Frontend 후속 검증을 인계한다**

  실제 가이드·챗봇 화면이 구현된 뒤 `@solia142`와 별도 happy-path E2E 한 건을 실행한다. Frontend E2E는 #61 Backend/AI 완료를 막지 않는다.

## 완료 판정

- Backend/AI one-cycle 필수: Task 0, Task 1, Task 2 본 작업, Task 2B `local-live-full`, Task 3~5가 PASS
- `local-live-ai`: Deferred이며 이번 MVP 구현·완료 조건에 포함하지 않음
- local network runner `local-live-full`: Task 2B의 CLOVA·OpenAI 전체 로컬 통합 검증이며 staging 완료를 대체하지 않음
- local 결과를 Issue 증거로 사용할 때는 clean worktree, commit SHA와 `evidence_qualified=true`가 필수
- Frontend E2E: 후속 별도 상태
- Production 배포 승인: 별도 gate이며 이번 결과로 승인되지 않음
