# AI One-Cycle Release Validation Design

| 항목 | 내용 |
| --- | --- |
| 관련 Issue/PR | 미정 — 구현 전 Backend·Infrastructure·Frontend 소유자 합의 필요 |
| 작성·AI 검증 담당 | 정현우 (`@ceohwj`) |
| 공동 리뷰 | Backend `@phina-io`, Frontend `@solia142`, 배포·아키텍처 `@hazelnutflavoured` |
| 문서 상태 | Draft / Blocked — 전용 staging 경계와 복구 가능한 cleanup 설계 구현 전 실행 금지 |
| 문서 역할 | 내부 Backend 통합 smoke 설계. Production 배포 승인 문서가 아님 |

## 1. 문서 목적

이 문서는 MVP 단계에서 현재 구현된 AI 기능이 격리된 staging 환경에서 한 번의 Backend 통합 흐름으로 정상 동작하는지 확인하기 위한 검증 설계를 정의한다.

이 검증의 목적은 운영 수준의 자동화·모니터링 체계를 만들거나 Production 배포 승인을 대체하는 것이 아니다. 비식별 합성 처방을 기준으로 다음 흐름이 실제 OpenAI API, Backend API와 staging DB를 거쳐 끝까지 연결되는지 확인하고 GitHub Issue에 Backend 통합 증거를 남기는 것이 목적이다.

```text
합성 OCR 완료 결과 준비
→ 처방 확정
→ 복약 가이드 생성
→ 채팅 세션 생성
→ 사용자 질문 전송
→ 챗봇 답변 생성
→ DB 저장 결과 확인
→ 합성 데이터 삭제
```

## 2. 현재 상태와 문제

현재 실제 OpenAI API를 호출하는 검증은 다음 두 테스트로 분리되어 있다.

- `backend/app/tests/guide_ai/test_smoke.py`
- `backend/app/tests/chat_ai/test_smoke.py`

두 테스트는 각각 Guide AI와 Chat AI 생성 모듈을 직접 호출한다. 따라서 다음 항목은 확인하지 않는다.

- 처방 확정부터 시작하는 실제 HTTP API 연결
- API 요청 사이에 ID가 올바르게 전달되는지 여부
- GUIDE와 CHAT_MESSAGE의 최종 DB 상태
- 실제 모델 ID와 프롬프트 버전이 DB에 저장되는지 여부
- 검증 종료 후 합성 데이터 정리

또한 `frontend/src/routes/AppRouter.tsx`에는 실제 가이드·챗봇 화면이 연결되어 있지 않다. Frontend E2E는 화면 구현 이후 Frontend owner가 별도 Issue와 계획으로 정의한다.

## 3. 목표

- staging FastAPI 배포 환경에서만 실행되는 명시적 검증 명령을 제공한다.
- 합성 OCR 완료 결과를 준비한 뒤 처방 확정부터 실제 HTTP API를 사용한다.
- 실제 OpenAI API로 가이드와 챗봇 답변을 각각 한 번 생성한다.
- API 응답뿐 아니라 DB를 다시 조회해 완료 상태, 실제 모델 ID와 프롬프트 버전을 확인한다.
- 실제 OpenAI 장애를 일부러 만들지 않고 기존 자동 테스트로 실패 상태 저장을 확인한다.
- 실행 결과를 비민감 JSON으로 출력하고 GitHub Issue 댓글에 사람이 읽기 쉬운 요약을 남긴다.
- 정상 종료에서는 합성 데이터를 삭제하고, 응답 유실·프로세스 중단 이후에도 동일 run ID로 재실행 가능한 cleanup 경로를 제공한다.

## 4. 제외 범위

- 새로운 AI 기능, 프롬프트 또는 모델 정책 개발
- API 요청·응답 형식, DB 구조 또는 상태 의미 변경
- 실제 환자·처방·대화 데이터 사용
- Production 환경 실행
- 실제 OCR 또는 CLOVA OCR 호출
- 잘못된 API Key, rate limit 또는 극단적으로 짧은 제한 시간을 이용한 실제 장애 유도
- 반복 실행 스케줄, 장기 결과 보관, 대시보드, 알림
- 성능, 부하, 동시성 또는 가용성 검증
- Frontend 가이드·챗봇 화면 구현
- Nginx·HTTPS·CORS·외부 ingress 검증
- Production 의료 안전 gate, 외부 Provider 전송 승인과 인프라 승인

## 5. 선택한 접근 방식

전용 staging Compose, application DB·control DB와 서비스별 비밀 주입이 구현된 뒤에만 `release-validator` one-off service에서 전용 CLI를 실행한다. 이 service는 `network_mode: service:fastapi`로 실행 중 FastAPI의 network namespace를 공유해 `127.0.0.1:8000`을 호출하지만 FastAPI process·service account와 control DB credential을 공유하지 않는다. 다음 명령은 구현 후 제공해야 할 최종 인터페이스이며 현재 저장소에서 바로 실행 가능한 명령이 아니다.

```bash
RELEASE_VALIDATION_IMAGE=<docker-inspect-fastapi-repository@sha256:repo-digest> \
docker compose \
  --project-name ah-staging \
  --project-directory infra/docker \
  --env-file envs/.staging.env \
  -f infra/docker/docker-compose.staging.yml \
  run --rm --no-deps \
  -e RELEASE_VALIDATION_ALLOWED=1 \
  -e RELEASE_VALIDATION_RUN_ID=<operator-generated-uuid> \
  -e RELEASE_VALIDATION_LOCAL_IMAGE_ID=<docker-inspect-local-image-id> \
  -e RELEASE_VALIDATION_REPO_DIGEST=<repository@sha256:docker-inspect-repo-digest> \
  -e RELEASE_VALIDATION_IMAGE_REVISION=<docker-inspect-oci-revision> \
  release-validator \
  uv run --no-sync python -m app.release_validation.ai_one_cycle_smoke
```

중단·응답 유실 후 wrapper는 cleanup image를 시작하기 전에 별도 digest로 고정된 staging ops image의 read-only resolver를 실행한다.

```bash
docker compose \
  --project-name ah-staging \
  --project-directory infra/docker \
  --env-file envs/.staging.env \
  -f infra/docker/docker-compose.staging.yml \
  run --rm --no-deps \
  -e RELEASE_VALIDATION_RUN_ID=<same-operator-generated-uuid> \
  release-ledger-resolver \
  uv run --no-sync python -m app.release_validation.ai_one_cycle_smoke --resolve-cleanup-image
```

Resolver는 read-only control credential로 contract version, run ID, full RepoDigest, OCI revision과 application schema revision만 strict JSON 한 건으로 반환한다. Wrapper는 run ID·version·형식과 단일 출력 여부를 검사하고 운영자 수기 전사 없이 그 값을 다음 명령의 Compose interpolation과 CLI 입력에 사용한다. Resolver image는 `RELEASE_CONTROL_IMAGE=<pinned-ops-repository@sha256:digest>`로 staging Compose에 고정하며 validation 대상 app image와 독립적으로 보존한다.

검증된 원래 immutable image를 DB-only `release-cleanup` one-off service에 지정해 다음 인터페이스로 실행한다.

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

wrapper는 저장소 절대경로를 계산해 Compose·env 경로와 검증된 provenance 입력을 전달할 뿐 원장을 직접 읽거나 변경하지 않는다. validation CLI가 advisory lock을 획득한 뒤 run ID, 원래 pull 가능한 full RepoDigest, 보조 local image ID·OCI revision과 schema revision을 app DB setup 전에 원장에 기록한다. cleanup CLI는 시작 후 ledger를 다시 조회해 resolver 출력과 비교한 뒤 application DB 접근을 허용한다. 명령 예시의 placeholder는 shell에 그대로 입력하지 않으며 wrapper가 검증된 값을 주입한다. RepoDigest가 없거나 registry에서 pull할 수 없는 image는 normal 실행 전에 차단한다.

`docker compose run --rm fastapi ...`나 실행 중 FastAPI container의 `docker compose exec`는 사용하지 않는다. 전자는 loopback FastAPI가 없고 후자는 application service account와 control-plane credential 경계를 섞는다. `release-validator`만 FastAPI network namespace를 공유하며 OpenAI Key 없이 application DB 검증용 credential과 control DB credential을 받는다.

전용 CLI 방식을 선택한 이유는 다음과 같다.

- 배포된 애플리케이션 image의 검증 코드를 사용하고, 실제 OpenAI 호출은 실행 중 FastAPI가 자신의 설정으로 수행한다.
- 전용 validation credential로 합성 데이터 준비·DB 재조회·삭제와 control ledger 전이를 수행한다.
- 외부에 노출되는 테스트 전용 API나 인증 우회 경로가 필요하지 않다.
- 현재 `backend/app/tests/conftest.py`가 test DB schema를 생성·삭제하므로 배포 환경에서 pytest를 직접 실행하는 위험을 피한다.

pytest 기반 live integration test는 배포 DB와 test DB 설정이 섞일 위험 때문에 사용하지 않는다. 외부 실행기 또는 staging 전용 검증 API도 MVP 범위에 비해 보안·운영 복잡도가 커서 사용하지 않는다.

전용 staging Compose와 DB가 없는 현재 상태에서는 이 검증을 실행하지 않는다. Production Compose의 `ENV` 값만 `staging`으로 바꿔 재사용하는 방식은 허용하지 않는다.

## 6. 구성요소

검증 코드는 다음 경계로 구성한다.

```text
backend/app/release_validation/
├── __init__.py
└── ai_one_cycle_smoke.py
```

초기 MVP에서는 한 파일에 구현하되 내부 역할은 작은 함수와 결과 `dataclass`로 구분한다. 아래 이름은 책임을 설명하기 위한 것이며 각각을 class나 별도 모듈로 만들라는 의미가 아니다. 구현 중 파일이 과도하게 커질 때만 같은 패키지 안에서 역할별로 나눈다.

### 6.1 Mode-specific Guard

normal smoke와 cleanup-only는 의존성이 다르므로 하나의 전역 guard를 공유하지 않는다.

두 모드가 공통으로 DB 접근 전에 확인할 조건은 다음과 같다.

- `ENV`는 `staging`이어야 한다.
- 운영자가 생성한 UUID 형식의 `RELEASE_VALIDATION_RUN_ID`가 있어야 하며, setup과 cleanup 모두 같은 값을 사용한다.
- staging 전용 DB host·user를 사용하고 `SELECT DATABASE()` 결과가 `RELEASE_VALIDATION_EXPECTED_DB_NAME`과 정확히 일치해야 한다.
- 실제 DB 이름은 `staging_` prefix를 가져야 하며 Production DB 이름·host deny-list와 하나라도 일치하면 즉시 중단한다.
- staging DB credential에는 지정된 staging DB 밖의 권한을 부여하지 않는다.
- Alembic 현재 revision이 cleanup logic이 지원하는 schema revision과 일치해야 한다.
- Production host·DB deny-list는 runtime override가 불가능한 배포 설정으로 주입한다.
- staging control DB identity·권한과 ledger schema version이 계약과 일치해야 한다.

normal smoke는 공통 guard에 더해 다음 조건을 요구한다.

- `RELEASE_VALIDATION_ALLOWED`는 `1`이어야 한다.
- 같은 network namespace의 FastAPI readiness가 성공해야 한다.
- non-secret 입력 `OPENAI_MODEL`은 FastAPI 배포 설정과 같은 MVP 합의값 `gpt-4o-mini`여야 한다. OpenAI Key는 validation service에 전달하지 않으며 FastAPI secret 주입 여부는 staging 배포 검사와 실제 호출 결과로 확인한다.
- build arg에서 bake된 `DEPLOY_COMMIT_SHA`가 있어야 한다.
- wrapper가 실행 중 컨테이너의 OCI `org.opencontainers.image.revision` label, local image ID와 full RepoDigest를 `docker inspect`로 각각 읽어 전달해야 한다.
- CLI는 wrapper가 전달한 revision을 bake된 `DEPLOY_COMMIT_SHA`와 비교하고 일치하지 않으면 중단한다.
- 같은 run ID의 ledger record가 없어야 하며, CLI가 advisory lock 안에서 최초 `STARTED` record를 만든다.
- 필수 조건을 만족하지 않으면 합성 데이터를 생성하기 전에 종료한다.

cleanup-only는 OpenAI Key·model, FastAPI readiness와 normal smoke 허용값을 요구하지 않는다. 대신 다음 조건만 추가로 요구한다.

- `RELEASE_CLEANUP_ALLOWED=1`
- DB 접근만 허용된 staging cleanup credential
- 실행 전에 staging 운영 원장에 보존한 원래 run ID, full RepoDigest·OCI revision과 schema revision
- 원장 record가 존재하고 입력 provenance·schema revision과 일치해야 한다. 원장에 없는 run ID는 성공 no-op이 아니라 오류다.
- 원래 full RepoDigest를 사용하는 `release-cleanup` one-off service. wrapper가 Compose image interpolation과 CLI 입력에 같은 원장값을 전달하고, CLI는 원장값·입력값·bake된 revision을 비교한다. 다른 image를 사용하려면 해당 schema revision과의 cleanup 호환성이 별도 리뷰·승인되어야 한다.

### 6.2 Staging Run Ledger와 상호 배제

애플리케이션 DB schema를 변경하지 않고 staging 인프라가 관리하는 별도 control DB에 `release_validation_runs` 원장과 append-only event를 둔다. schema·상태·전이·권한·90일 보존의 authoritative 정의는 [`docs/contracts/release-validation-ledger.md`](../../contracts/release-validation-ledger.md)다. FastAPI application service account와 host wrapper는 원장을 읽거나 변경할 수 없다. `release-validator`와 `release-cleanup`에서 실행되는 동일 validation CLI만 record를 생성·전이한다. Resolver는 provenance read-only, migration process는 advisory lock·unresolved 조회, retention role은 90일이 지난 `RESOLVED` record·event 삭제만 할 수 있다.

- `run_id` 기본키
- 원래 pull 가능한 full RepoDigest, 보조 local image ID와 OCI revision
- 시작 당시 애플리케이션 schema revision
- `STARTED`, `SETUP_IN_PROGRESS`, `ANCHOR_COMMITTED`, `RESOLUTION_PENDING`, `CLEANUP_IN_PROGRESS`, `RESOLVED` 중 하나인 resolution state
- 예상 합성 anchor의 exact email·phone·object key와, fixture commit 뒤 확정한 후손 ID·개수 hash
- 생성·최종 갱신 시각과 민감정보가 없는 실패 단계

normal validation CLI는 애플리케이션 DB에 쓰기 전에 `STARTED` record를 원자적으로 생성한다. fixture 값과 예상 anchor identity를 원장에 기록하고 `SETUP_IN_PROGRESS`로 먼저 전이한 뒤 애플리케이션 fixture를 commit한다. commit 뒤 exact anchor와 후손 identity·개수를 확인하고 hash를 원장에 저장하면서 `ANCHOR_COMMITTED`로 전이한다. 결과가 불명확하거나 cleanup이 남으면 `RESOLUTION_PENDING`으로 전이한다.

cleanup은 애플리케이션 row를 삭제하기 전에 원장에 대상 identity·개수 hash를 확정하고 `CLEANUP_IN_PROGRESS`로 먼저 전이한다. app DB cleanup commit과 새 session의 0건 확인 뒤 `RESOLVED`로 전이한다. 두 DB commit을 원자적이라고 가정하지 않고 다음 idempotent saga 복구 규칙을 적용한다.

| 원장 상태 | app DB 관찰 | 복구 동작 |
| --- | --- | --- |
| `STARTED` | row 0건 | app DB write 전 중단으로 확인하고 `RESOLVED` |
| `SETUP_IN_PROGRESS` | exact 예상 anchor 존재 | 해당 anchor를 채택하고 identity를 확정한 뒤 cleanup 계속 |
| `SETUP_IN_PROGRESS` | row 0건 | fixture commit 전 중단으로 확인하고 `RESOLVED` |
| `ANCHOR_COMMITTED` 또는 `RESOLUTION_PENDING` | exact anchor 존재 | 원장 identity와 비교한 뒤 cleanup 시작 |
| `ANCHOR_COMMITTED` 또는 `RESOLUTION_PENDING` | anchor 없음 | 무결성 오류 `FAIL` |
| `CLEANUP_IN_PROGRESS` | 원장 identity와 일치하는 row 존재 | FK 역순 cleanup 재개 |
| `CLEANUP_IN_PROGRESS` | 관련 row 0건 | 새 session에서 다시 확인한 뒤 `RESOLVED` |
| `RESOLVED` | 관련 row 0건 | idempotent `PASS` no-op |

원장 record 생성이나 write-ahead 전이에 실패하면 다음 app DB write를 수행하지 않는다. 관찰된 anchor·후손이 원장 identity와 다르면 자동 채택·삭제하지 않고 `FAIL`로 중단한다.

normal·cleanup validation CLI와 staging migration process는 control DB의 같은 advisory lock `ah_staging_release_validation`을 전용 connection으로 획득하고 전체 작업 동안 유지한다. 제한 시간 안에 lock을 획득하지 못하면 app DB write 전에 non-zero로 종료한다. 각 process는 자신이 소유한 lock connection을 감시하며 connection이 끊기거나 lock 소유 확인이 실패하면 다음 app DB write를 중단하고 fail-closed로 끝낸다. write-ahead 원장 상태가 다음 실행의 복구 대상을 보존한다. migration process는 이 lock 안에서 unresolved record가 0건인지 조회하고 migration이 끝날 때까지 lock을 유지한다. normal·cleanup은 migration이 lock을 보유한 동안 시작할 수 없다.

staging migration 전에는 같은 lock 안에서 unresolved record가 0건이어야 한다. 잔존 record가 있으면 원장에 기록된 원래 image로 cleanup을 완료한 뒤 migration을 진행한다.

Production에서는 실행할 수 없다. 별도의 강제 실행 옵션도 이번 범위에서는 제공하지 않는다.

### 6.3 SyntheticDataFixture

검증에 필요한 최소 시작 데이터를 DB에 준비한다.

- 운영자가 전달한 고유 run ID를 사용한다. 프로세스가 중단돼도 같은 ID로 cleanup-only 명령을 재실행할 수 있어야 한다.
- run ID가 포함된 합성 사용자를 생성한다.
- 합성 의료문서와 완료된 OCR 작업을 생성한다.
- 처방일과 약물 필드를 모두 사용자 확인 완료 상태로 생성한다.
- 질문·답변과 무관한 추가 의료정보는 생성하지 않는다.
- 합성 사용자의 email·phone과 의료문서 object key에 run ID를 포함해 DB에 지속되는 정리 anchor로 사용한다.

### 6.4 OneCycleRunner

합성 OCR 완료 결과가 준비된 이후 실제 HTTP API를 순서대로 호출한다.

- 합성 계정 로그인
- 처방 확정
- 복약 가이드 생성
- 채팅 세션 생성
- 합성 질문 전송

각 요청이 성공하면 응답의 식별자를 다음 요청에 전달한다. HTTP connect timeout은 5초, read timeout은 `OPENAI_TIMEOUT_SECONDS + M` 이상으로 명시한다. `M`은 검증 CLI의 양수 처리 여유 설정이며 실행 기록에 실제 값을 남긴다. 응답을 받지 못한 transport 실패는 우선 `OUTCOME_UNKNOWN`으로 분류하고 단계별 reconciliation을 수행한다. 로그인은 안전하게 재시도하고, 처방·가이드·채팅 세션·메시지는 exact run anchor와 부모 관계에서 이번 단계가 만든 terminal row를 찾아 식별자를 복구한다. terminal 성공과 DB 기준을 확인하면 해당 단계만 `PASS`로 바꾸고 아직 실행하지 않은 다음 단계를 계속한다. terminal 실패는 전체 execution `FAIL`, 제한 시간 만료·중복 후보·identity 불일치는 `OUTCOME_UNKNOWN`을 유지하며 이후 정상 흐름을 실행하지 않는다.

### 6.5 DatabaseVerifier

API 응답과 분리된 새 DB session으로 저장 결과를 다시 조회한다.

- 확정 처방의 약물 값과 표시 순서
- GUIDE의 완료 상태, 내용 존재 여부, 실제 모델 ID와 프롬프트 버전
- USER와 ASSISTANT 메시지의 순서
- ASSISTANT의 완료 상태, 내용 존재 여부, 실제 모델 ID와 프롬프트 버전
- 정상 완료 row의 실패 코드와 실패 메시지가 비어 있는지 여부

API가 성공 응답을 반환했다는 사실만으로 DB 저장 성공을 판단하지 않는다.

### 6.6 SmokeResult

단계별 결과를 모아 비민감 JSON 한 건으로 터미널에 출력한다.

- 실행 모드: `NORMAL`, `CLEANUP_ONLY`
- 실행 상태: `PASS`, `FAIL`, `OUTCOME_UNKNOWN`, `NOT_RUN`
- cleanup 상태: `PASS`, `PENDING`, `FAIL`
- 종합 상태: `PASS`, `FAIL`, `RECOVERY_REQUIRED`
- 실행 환경
- commit SHA, 실행 중인 full RepoDigest·local image ID·OCI revision
- 시작·종료 시각
- 단계별 PASS 또는 FAIL
- 실제 모델 ID와 프롬프트 버전
- 생성 내용의 길이
- 합성 데이터 삭제 결과

API Key, 로그인 token, 질문 전문, 가이드 전문과 챗봇 답변 전문은 포함하지 않는다. JSON은 정상적으로 제어 가능한 종료에서 stdout에 정확히 한 번 출력하고, 제한된 진단 메시지는 stderr에만 출력한다.

normal mode의 상태와 종료 코드는 다음과 같다. execution과 cleanup은 서로 독립적으로 보존하며 cleanup 실패가 execution 결과를 덮어쓰지 않는다.

| execution | cleanup | overall | exit code |
| --- | --- | --- | --- |
| `PASS` | `PASS` | `PASS` | `0` |
| `PASS` | `PENDING` 또는 `FAIL` | `RECOVERY_REQUIRED` | non-zero |
| `FAIL` | `PASS` | `FAIL` | non-zero |
| `FAIL` | `PENDING` 또는 `FAIL` | `RECOVERY_REQUIRED` | non-zero |
| `OUTCOME_UNKNOWN` | 모든 값 | `RECOVERY_REQUIRED` | non-zero |

cleanup-only mode는 `execution=NOT_RUN`을 사용한다. 원장과 사후 0건 검증이 모두 성립해 `cleanup=PASS`이면 `overall=PASS`, exit code `0`이다. `cleanup=PENDING`이면 `overall=RECOVERY_REQUIRED`, `cleanup=FAIL`이면 `overall=FAIL`이며 둘 다 non-zero다. cleanup-only는 이전 normal 실행의 execution 값을 새로 판정하거나 `PASS`로 조작하지 않는다.

## 7. 합성 시나리오

합성 값은 실제 환자나 실제 의약품으로 오인되지 않도록 명시적인 이름을 사용한다.

| 항목 | 값 |
| --- | --- |
| 처방일 | `2026-08-21` |
| 약물명 | `합성의약품 에이` |
| 1회 복용량 | `1` |
| 단위 | `정` |
| 하루 복용 횟수 | `2` |
| 복용 시점 | `식후` |
| 복용 기간 확인값 | `3` |
| 처방 결과 기대값 | `duration_days=3` (`3일` 표시는 렌더링 결과에서만 사용) |
| 질문 | `이 합성 약을 복용할 때 일반적으로 확인해야 할 점을 짧게 알려주세요.` |

OCR은 실제로 실행하지 않는다. fixture는 현재 DB constraint를 만족하는 다음 값을 명시적으로 준비한다.

| Row | 필수 합성값 |
| --- | --- |
| User | hyphen 없는 32자 run ID를 포함해 40자 이하인 고유 email, run ID hash로 만든 20자 이하 고유 phone, 합성 name, gender, birthday, 해시 비밀번호, `is_active=true` |
| MedicalDocument | run ID가 포함된 object key, 합성 filename·MIME type, `file_size_bytes > 0`, `UPLOADED` |
| OcrJob | `COMPLETED`, non-null `started_at`·`completed_at`, null 오류 필드 |
| 처방일 ExtractedField | `medication_index=0`, `PRESCRIBED_DATE`, `CONFIRMED`, 확인값 `2026-08-21`, non-null `confirmed_at` |
| 약물 ExtractedField | `medication_index=1`, 각 field type별 숫자·문자 확인값, `CONFIRMED`, non-null `confirmed_at` |

숫자형 확인값은 parser 입력에 맞춰 `1`, `2`, `3`처럼 단위 없는 문자열로 저장한다. `정`, `식후`, `3일` 같은 표시 텍스트와 DB 확인값을 혼용하지 않는다.

이 경계로 OCR 인식 품질과 외부 OCR 서비스 상태가 AI one-cycle 결과에 영향을 주지 않도록 한다.

## 8. 실행 흐름

### 8.1 사전 확인

1. 환경 가드를 검사한다.
2. `GET http://127.0.0.1:8000/api/openapi.json`이 `200`을 반환하는지 확인해 같은 컨테이너의 FastAPI 서버가 준비됐는지 검사한다.
3. DB identity·권한 범위와 Alembic head를 확인한다.
4. bake된 commit SHA와 실행 중인 full RepoDigest·local image ID·OCI revision을 확인한다.
5. 운영자가 전달한 run ID가 기존 활성 run과 충돌하지 않는지 확인한다.

### 8.2 합성 데이터 준비

1. 비밀번호가 해시된 합성 사용자를 만든다.
2. 합성 의료문서를 만든다.
3. `COMPLETED` OCR 작업을 만든다.
4. 처방일과 약물 필드를 `CONFIRMED` 상태로 만든다.
5. 한 transaction에서 합성 시작 데이터를 commit하고 setup session을 닫는다. commit이 실패하면 rollback한 뒤 전체 결과를 `FAIL`로 처리하며 HTTP API를 호출하지 않는다.

### 8.3 실제 API 호출

1. `POST /api/v1/auth/login`
2. `POST /api/v1/documents/{document_id}/prescription`
3. `POST /api/v1/guides`
4. `POST /api/v1/prescriptions/{prescription_id}/chat-sessions`
5. `POST /api/v1/chat-sessions/{session_id}/messages`

HTTP client의 base URL은 컨테이너 내부 주소인 `http://127.0.0.1:8000`을 사용하고 위의 `/api/v1/...` 전체 경로를 호출한다. base URL과 요청 경로에 `/api/v1`을 동시에 넣지 않는다. 로그인에서 받은 token은 메모리에서만 보관하고 결과 또는 로그에 출력하지 않는다. 이 loopback 검증은 Nginx·HTTPS·CORS와 외부 ingress를 검증하지 않는다.

### 8.4 DB 재확인

1. 모든 HTTP 응답이 끝난 후 API 요청과 다른 새 DB session을 연다.
2. 확정 처방과 약물을 다시 조회한다.
3. GUIDE row를 다시 조회한다.
4. 채팅 세션의 메시지를 순서대로 다시 조회한다.
5. 성공 기준을 모두 검사하고 확인 session을 닫는다.

### 8.5 데이터 정리

정상 종료와 처리 가능한 실패에서는 `finally` 경로에서 새 cleanup session을 열고 이번 실행 데이터 삭제를 시도한다. SIGKILL·컨테이너 재시작에서는 `finally`가 보장되지 않으므로 cleanup을 "항상" 수행한다고 표현하지 않는다. staging 운영 원장에 기록된 run ID와 원래 image로 DB-only `release-cleanup` service에서 `--cleanup-only` 명령을 재실행할 수 있어야 한다. 이 경로는 FastAPI·OpenAI에 의존하지 않는다.

정리 대상은 응답에서 받은 ID가 아니라 run ID가 포함된 합성 user를 root로 다시 조회한다. user → document → OCR job → prescription → guide/chat 후손을 FK 관계로 탐색해 소유 관계를 확인한다. transport 결과가 `OUTCOME_UNKNOWN`이면 외부 호출의 최대 종료시간까지 terminal 상태를 poll하며, `GENERATING` row나 진행 중 요청이 남아 있으면 삭제하지 않고 cleanup 상태 `PENDING`으로 종료한다.

외래키 관계를 고려해 자식 데이터부터 삭제한다.

1. Chat citation과 message
2. Chat session
3. Guide citation과 guide
4. Medication과 prescription
5. Extracted field와 OCR job
6. Medical document
7. 합성 user

정리 대상은 정확히 일치하는 run ID anchor와 확인된 부모 관계로 제한한다. prefix 전체, 날짜 범위, 전체 테이블 조건으로 삭제하지 않는다. 원장에 대상 ID·개수 hash를 기록하고 `CLEANUP_IN_PROGRESS`로 전이한 뒤, 발견한 ID별 예상 삭제 개수와 실제 삭제 개수를 비교해 일치할 때만 cleanup transaction을 commit한다. commit 후 새 session으로 같은 run ID의 user와 모든 후손이 0건인지 확인한다. 원장에 없는 run ID, 원장 identity와 DB 상태 불일치 또는 `ANCHOR_COMMITTED`·`RESOLUTION_PENDING`인데 anchor가 없는 경우는 `FAIL`이다. `STARTED`, `SETUP_IN_PROGRESS`, `CLEANUP_IN_PROGRESS`, `RESOLVED`의 0건은 6.2의 saga 표에 따라서만 안전하게 `RESOLVED` 또는 성공 no-op으로 처리한다. 불일치나 commit 실패는 cleanup `PENDING` 또는 `FAIL`로 기록하며 cleanup-only 재실행 없이는 검증을 완료할 수 없다.

## 9. 성공 기준

### 9.1 처방 확정

- HTTP 상태가 `201`이다.
- 처방이 확정 상태로 생성된다.
- 약물명이 `합성의약품 에이`이다.
- 복용량, 단위, 횟수, 시점과 기간이 합성 입력과 일치한다.

### 9.2 가이드

- 생성 API의 HTTP 상태가 `201`이다.
- DB의 GUIDE 상태가 `COMPLETED`이다.
- 가이드 내용이 비어 있지 않다.
- 가이드에 `합성의약품 에이`와 `식후`가 포함된다.
- 실제 모델 ID가 저장되고 `gpt-4o-mini` prefix와 일치한다.
- 프롬프트 버전이 `guide-prompt-v1`이다.
- 실패 코드와 실패 메시지가 비어 있다.

### 9.3 챗봇

- 채팅 세션 생성 API의 HTTP 상태가 `201`이다.
- 채팅 세션 상태가 `ACTIVE`이다.
- 질문 전송 API의 HTTP 상태가 `201`이다.
- USER와 ASSISTANT 메시지가 올바른 순서로 저장된다.
- ASSISTANT 상태가 `COMPLETED`이다.
- 답변 내용이 비어 있지 않다.
- 실제 모델 ID가 저장되고 `gpt-4o-mini` prefix와 일치한다.
- 프롬프트 버전이 `chat-prompt-v1`이다.
- 실패 코드와 실패 메시지가 비어 있다.

### 9.4 보안과 데이터 정리

- 출력에 API Key와 로그인 token이 없다.
- 출력에 질문, 가이드와 답변 전문이 없다.
- 이번 실행에서 만든 합성 데이터가 모두 삭제되고 새 session의 사후 조회가 0건이다.

모든 필수 기준과 데이터 정리가 성공해야 전체 결과가 `PASS`이다.

## 10. 실패 처리

실패 단계는 다음 고정 이름으로 구분한다.

```text
ENVIRONMENT_CHECK
SERVER_READINESS
SYNTHETIC_DATA_SETUP
LOGIN
PRESCRIPTION_CONFIRM
GUIDE_GENERATION
GUIDE_DB_CHECK
CHAT_SESSION
CHAT_MESSAGE
CHAT_DB_CHECK
OUTCOME_RECONCILIATION
CLEANUP
```

실패 결과에는 다음 정보만 포함할 수 있다.

- 실패 단계
- HTTP 상태 코드
- API의 안전한 오류 코드
- trace ID
- 예외 class 이름
- 합성 데이터 삭제 결과

OpenAI SDK 원문 오류, HTTP 요청 body, 질문, 가이드와 답변 전문은 출력하지 않는다. 실패 JSON은 stdout에 한 번 출력하고 process는 non-zero로 종료한다.

확정된 중간 단계 실패가 발생하면 이후 정상 흐름 호출은 실행하지 않는다. 응답 유실은 해당 단계만 `OUTCOME_UNKNOWN`으로 기록하고 `OUTCOME_RECONCILIATION`을 거친다. 제한 시간 안에 그 단계의 terminal 성공 row와 exact identity가 확인되면 식별자를 복구해 해당 단계를 `PASS`로 바꾸고 다음 미실행 단계부터 계속한다. terminal 실패 row가 확인되면 execution을 `FAIL`로 전이하고, 제한 시간 만료·non-terminal·중복 후보·anchor 무결성 오류가 남으면 execution `OUTCOME_UNKNOWN`을 유지한다. 모든 필수 단계가 각각 `PASS`이고 최종 DB 검증까지 성공한 경우에만 execution을 `PASS`로 판정한다.

정상 종료에서는 합성 데이터 삭제를 시도한다. 정상 흐름이 성공했더라도 데이터 삭제 또는 사후 0건 검증이 실패하면 `execution=PASS`를 보존하고 cleanup은 `PENDING` 또는 `FAIL`, overall은 `RECOVERY_REQUIRED`로 기록한다. 프로세스 강제 종료 뒤에는 동일 run ID의 cleanup-only 명령이 성공하기 전까지 normal 실행을 완료로 판정하지 않는다.

## 11. 실제 OpenAI 실패 검증 경계

live one-cycle은 정상 흐름 확인에만 사용한다. 다음 실패는 실제 OpenAI 환경을 조작하지 않고 기존 결정적 자동 테스트로 확인한다.

- timeout 시 GUIDE가 `FAILED`로 저장되는지
- Provider 오류 시 GUIDE가 `FAILED`로 저장되는지
- Chat 실패 시 USER와 FAILED ASSISTANT 메시지가 함께 저장되는지
- 실패한 결과의 content, `model_name`과 프롬프트 버전이 비어 있는지
- 정해진 안전한 오류 코드와 오류 문구만 저장되는지

현재 Chat 회귀 테스트는 위 저장 조건을 확인한다. Guide 회귀 테스트에는 `content`, `model_name`, `prompt_version`, `error_code`, `completed_at`을 확인하는 assertion을 보강한 뒤 릴리스 증거로 사용한다.

검증 대상 테스트는 다음과 같다.

```bash
uv run pytest \
  backend/app/tests/guide_ai/test_backend_contract.py::test_backend_contract_maps_generation_errors_and_marks_failed \
  backend/app/tests/repositories/test_guide_repository.py::test_mark_failed_persists_after_subsequent_rollback \
  backend/app/tests/chat_apis/test_chat_message_api.py::test_failed_send_is_requeried_as_exact_user_and_failed_assistant_pair \
  backend/app/tests/repositories/test_chat_repository.py::test_commit_failed_message_pair_persists_exactly_one_user_failed_assistant_pair_after_rollback \
  -q
```

## 12. 결과 출력과 Issue 기록

### 12.1 CLI 출력

성공 시 다음 형태의 JSON을 터미널에 한 번 출력한다.

```json
{
  "mode": "NORMAL",
  "overall": "PASS",
  "execution": "PASS",
  "run_id": "11111111-1111-4111-8111-111111111111",
  "environment": "staging",
  "commit_sha": "abc1234",
  "repo_digest": "example.invalid/app@sha256:synthetic-example",
  "local_image_id": "sha256:synthetic-local-image-id",
  "image_revision": "abc1234",
  "started_at": "2026-08-21T10:00:00+09:00",
  "completed_at": "2026-08-21T10:00:18+09:00",
  "steps": {
    "prescription_confirm": "PASS",
    "guide_generation": "PASS",
    "guide_db_check": "PASS",
    "chat_session": "PASS",
    "chat_message": "PASS",
    "chat_db_check": "PASS"
  },
  "guide": {
    "status": "COMPLETED",
    "model_name": "gpt-4o-mini-actual-model-id",
    "prompt_version": "guide-prompt-v1",
    "content_length": 320
  },
  "chat": {
    "status": "COMPLETED",
    "model_name": "gpt-4o-mini-actual-model-id",
    "prompt_version": "chat-prompt-v1",
    "content_length": 180
  },
  "cleanup": "PASS"
}
```

실패 시 정상 완료되지 않은 단계의 상세 객체는 생략하고 실패 단계와 안전한 오류 정보만 출력한다.

### 12.2 GitHub Issue 댓글

MVP 단계에서는 별도 서버 파일, 장기 보관 또는 대시보드를 만들지 않는다. 실행자가 터미널 결과를 바탕으로 다음 요약을 Issue 댓글에 남긴다.

```markdown
## AI one-cycle 검증 결과

- 환경: staging
- Run ID: 11111111-1111-4111-8111-111111111111
- Commit: abc1234
- RepoDigest: example.invalid/app@sha256:synthetic-example
- Local image ID: sha256:synthetic-local-image-id
- 실행 시각: 2026-08-21 10:00 KST
- 종합 결과: PASS
- 실행 결과: PASS
- Cleanup: PASS
- 처방 확정: PASS
- 가이드 생성·DB 확인: PASS
  - 모델 ID: gpt-4o-mini-actual-model-id
  - 프롬프트: guide-prompt-v1
- 챗봇 생성·DB 확인: PASS
  - 모델 ID: gpt-4o-mini-actual-model-id
  - 프롬프트: chat-prompt-v1
- 실패 상태 저장 회귀 테스트: PASS
- 합성 데이터 삭제: PASS
```

## 13. 테스트 전략

### 13.1 CLI 단위 테스트

실제 OpenAI API를 사용하지 않는 자동 테스트로 다음을 확인한다.

- staging이 아니면 실행을 거부한다.
- 실행 허용 환경변수가 없으면 실행을 거부한다.
- staging Compose가 OpenAI Key를 FastAPI에만 주입하고 validation·cleanup·Worker에는 전달하지 않는다.
- FastAPI 준비 확인이 실패하면 합성 데이터를 만들지 않는다.
- 실제 API를 정해진 순서로 호출한다.
- 이전 단계의 ID를 다음 단계에 정확히 전달한다.
- 합성 데이터 setup을 commit하고 session을 닫은 뒤 HTTP 호출을 시작한다.
- DB에서 모델 ID와 프롬프트 버전을 다시 확인한다.
- 로그인과 각 write endpoint 직후의 응답 유실을 각각 주입하고, exact 후손 ID를 복구해 남은 단계를 재개한다.
- process 중단 뒤 동일 run ID의 cleanup-only 명령을 재실행할 수 있다.
- 삭제 대상의 합성 데이터 소유 관계와 삭제 개수를 확인한다.
- 데이터 삭제 후 새 session에서 같은 run ID의 후손이 0건인지 확인한다.
- 원장에 없는 유효한 UUID, unresolved 원장의 anchor 유실과 잘못된 run ID는 성공 no-op이 아니다.
- unresolved 원장 record가 있으면 staging migration이 차단되고, normal·cleanup·migration이 같은 advisory lock으로 직렬화된다.
- fixture commit 직후와 cleanup commit 직후 process kill을 주입해 saga 상태로 복구된다.
- image ID와 RepoDigest를 혼동하거나 RepoDigest가 없으면 normal 실행을 차단한다.
- lock 획득 timeout·획득 실패·실행 중 connection loss는 app DB write를 중단하고 non-zero로 끝난다.
- reconciliation은 해당 단계 terminal 성공 후 남은 단계를 모두 완료해야 전체 `PASS`, terminal 실패는 `FAIL`, 제한 시간 만료는 `OUTCOME_UNKNOWN`으로 판정한다.
- JSON에 비밀정보와 생성 내용 전문이 포함되지 않는다.
- normal은 execution·cleanup 조합표에 따라 종료하고, cleanup-only는 `execution=NOT_RUN`, `cleanup=PASS`일 때만 exit code `0`이다.

### 13.2 관련 회귀 검사

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy backend/app ai_worker
bash scripts/ci/run_test.sh
```

### 13.3 staging live 검증

자동 테스트와 정적 검사를 통과한 동일한 배포 이미지에서 전용 CLI를 한 번 실행한다. 기존 Guide·Chat 개별 live smoke는 하위 AI 모듈 확인 용도로 유지하며 새 one-cycle은 이를 대체하지 않는다. 결과는 Backend 통합 검증 증거일 뿐 Production 배포 승인이나 기능 Issue 전체 완료를 의미하지 않는다.

## 14. Frontend E2E 후속 검증

현재 Frontend에는 실제 가이드·챗봇 화면과 라우트가 없으므로 Backend one-cycle CLI에 Frontend를 포함하지 않는다.

화면 구현 이후 동일 합성 시나리오로 다음 happy path 한 건을 공동 검증할 수 있다. Frontend E2E는 이 CLI가 만든 데이터를 재사용하지 않는다. 도구, 의존성, 합성 fixture 준비·삭제와 screenshot·trace 민감정보 제거 정책은 Frontend owner가 별도 Issue와 계획에서 확정한다.

```text
로그인
→ 준비된 합성 처방 확정
→ 가이드 생성 및 화면 표시
→ 채팅 세션 생성
→ 질문 전송
→ 챗봇 답변 화면 표시
```

AI 응답 문장 전체가 매번 동일한지는 검사하지 않는다. 다음 사용자 관찰 결과를 확인한다.

- 가이드 화면에 합성 약물명이 표시된다.
- 가이드 내용이 비어 있지 않다.
- 채팅 질문 전송 후 챗봇 답변이 표시된다.
- 화면 이동과 API 요청이 정상 완료된다.
- 브라우저 console에 처리되지 않은 오류가 없다.
- 사람이 화면에 표시된 가이드와 챗봇 답변을 확인해 합성 처방의 약 중단, 용량 또는 복용 시간 변경을 임의로 권하지 않는지 확인한다.

현재 저장소에는 실행 가능한 브라우저 E2E stack이 없으므로 이 문서에서 Frontend 완료 상태를 주장하지 않는다. Frontend 화면과 E2E는 별도 owner-owned gate로 관리한다.

## 15. 보안과 의료 안전

- 실제 환자, 처방전, 의약품 또는 대화 데이터를 사용하지 않는다.
- `OPENAI_API_KEY`는 staging FastAPI 컨테이너에만 배포 비밀정보로 주입한다.
- 현재 production Compose처럼 FastAPI와 AI Worker가 같은 `.env` 전체를 받는 방식을 staging에 복제하지 않는다. staging Compose는 OpenAI Key를 FastAPI에만 allowlist로 전달하고 AI Worker에는 전달하지 않는다.
- Frontend와 AI Worker에는 이번 검증을 이유로 OpenAI Key를 추가하지 않는다.
- `envs/example.prod.env`와 `envs/example.local.env`의 credential 형태 예시 값은 비밀이 아닌 명확한 placeholder로 교체한다. 실제 사용 이력이 있다면 공개 Issue에 값을 남기지 않고 `SECURITY.md` 절차에 따라 폐기·재발급한 뒤 staging 검증을 진행한다.
- token은 메모리에서만 사용하고 로그나 결과에 남기지 않는다.
- 질문·가이드·답변 전문을 공개 로그나 Issue 댓글에 남기지 않는다.
- 실행 결과에는 길이, 상태, 모델 ID와 프롬프트 버전만 남긴다.
- 합성 데이터는 정상 종료 시 삭제하고, 중단·응답 유실 시 run ID 기반 cleanup-only 명령으로 복구한다.
- E2E를 위한 인증 우회 또는 staging 전용 공개 API를 추가하지 않는다.
- Backend CLI는 연결, 상태 저장과 생성 metadata만 판정한다. `docs/deployment.md`의 근거·검증 또는 코드로 강제되는 제한 모드와 재현 가능한 안전 gate를 이 smoke의 PASS로 대체하지 않는다.

## 16. 담당과 리뷰 경계

- `@ceohwj`: 합성 시나리오, AI 안전 기준, 실제 OpenAI 검증 실행과 결과 판정
- `@phina-io`: `backend/app/` 안의 전용 CLI, 합성 DB 데이터 준비·조회·삭제 로직 구현과 리뷰
- `@solia142`: 실제 Frontend 가이드·챗봇 화면 구현과 후속 E2E 공동 검증

이 작업은 기존 공유 계약을 변경하지 않는다. 구현 중 API 형식, DB 구조 또는 상태 의미를 변경해야 하는 상황이 발견되면 one-cycle 검증 작업을 중단하고 관련 CODEOWNER와 별도 계약 변경으로 분리한다.

## 17. Backend/AI 통합 검증 완료 판정

다음 조건을 모두 만족하면 Backend/AI one-cycle 검증만 완료된다.

- staging 전용 CLI 환경 가드가 동작한다.
- 합성 OCR 완료 결과에서 처방 확정이 성공한다.
- 실제 OpenAI 가이드 생성과 DB 재확인이 성공한다.
- 실제 OpenAI 챗봇 응답과 DB 재확인이 성공한다.
- `gpt-4o-mini` 계열 실제 모델 ID와 두 프롬프트 버전이 확인된다.
- commit SHA와 실행 중인 full RepoDigest·local image ID·OCI revision이 각각 확인된다.
- 결정적 실패 저장 테스트가 통과한다.
- 합성 데이터 삭제와 새 session의 사후 0건 검증이 성공한다.
- 비민감 결과가 JSON으로 출력된다.
- 검증 결과가 GitHub Issue 댓글에 기록된다.

이 결과는 기존 Guide·Chat 개별 live smoke, Frontend E2E, `docs/deployment.md`의 의료 안전·외부 전송·수용량·인프라 승인과 Production 배포 승인을 완료하거나 대체하지 않는다. 기능 Issue 완료와 Production 배포 승인은 각각의 authoritative checklist에서 별도로 판정한다.
