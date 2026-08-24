# AI One-Cycle Release Validation Design

| 항목 | 내용 |
| --- | --- |
| 관련 작업 | 선행 Issue [#67 MySQL에서 PostgreSQL로 DB 전환](https://github.com/AI-HealthCare-05/AH_05_04/issues/67), 후속 Backend/AI one-cycle 구현 Issue·PR |
| 작성·AI 검증 담당 | 정현우 (`@ceohwj`) |
| 공동 리뷰 | Backend `@phina-io`, Frontend `@solia142`, 배포·아키텍처 `@hazelnutflavoured` |
| 문서 상태 | Draft / Blocked by #67 — PostgreSQL 전환 merge와 회귀 검사 통과 후 구현 시작 |
| 문서 역할 | MVP Backend/AI 통합 smoke 설계. Production 배포 승인 문서가 아님 |

## 1. 목적

이 검증의 목적은 새로운 AI 기능이나 운영 자동화 체계를 만드는 것이 아니다. 현재 구현된 기능이 실제 OpenAI API와 Backend API를 거쳐 다음 한 사이클로 연결되는지 staging에서 한 번 확인하고, 사람이 다시 볼 수 있는 비민감 기록을 남기는 것이 목적이다.

```text
합성 OCR 완료 결과 준비
→ 처방 확정
→ 복약 가이드 생성
→ 채팅 세션 생성
→ 사용자 질문 전송
→ 챗봇 답변 생성
→ DB 저장 결과 확인
→ 실행 전용 staging DB 폐기
```

`Backend/AI one-cycle 검증 완료`, `기능 Issue 완료`, `Production 배포 승인`은 서로 다른 상태다. 이 문서의 검증 성공만으로 기능 전체 또는 Production 배포가 승인되지는 않는다.

## 2. 현재 상태

실제 OpenAI API를 호출하는 테스트는 다음 두 개로 분리되어 있다.

- `app/tests/guide_ai/test_smoke.py`
- `app/tests/chat_ai/test_smoke.py`

두 테스트는 Guide AI와 Chat AI 생성 모듈을 각각 직접 호출한다. 따라서 처방 확정부터 시작하는 실제 HTTP 흐름, API 사이의 ID 전달, GUIDE와 CHAT_MESSAGE의 최종 DB 상태, 실제 모델 ID·프롬프트 버전 저장은 확인하지 않는다. 환경변수가 없으면 두 테스트 모두 skip된다.

현재 저장소에는 staging Compose와 one-cycle CLI가 없으며, `frontend/src/routes/AppRouter.tsx`에도 실제 가이드·챗봇 화면이 연결되어 있지 않다. Frontend happy-path E2E는 화면 구현 이후 별도 owner-owned 작업으로 진행한다.

Issue #67에서 `@Jye-rookie`가 application DB를 PostgreSQL로 전환하고 driver, 연결 URL, local·production Compose, env example, Alembic, CI와 핵심 Backend API smoke를 함께 검증하고 있다. one-cycle 작업은 이 DB 전환을 중복 구현하지 않으며 #67이 merge되고 PostgreSQL 회귀 검사가 통과한 기준 위에서 시작한다.

#67의 핵심 API smoke는 DB 엔진 전환 후 기존 API 동작 보존을 확인하는 증거다. 이 문서의 smoke는 실제 `OPENAI_API_KEY`를 사용해 가이드·챗봇 생성과 실제 모델 ID·프롬프트 버전 저장을 확인하므로 목적과 완료 증거가 다르다.

## 3. 목표

- Production과 분리된 실행 전용 staging 환경에서만 검증한다.
- 합성 OCR 완료 결과를 준비한 뒤 처방 확정부터 실제 HTTP API를 사용한다.
- 실제 OpenAI API로 가이드와 챗봇 답변을 각각 한 번 생성한다.
- API 응답과 별개로 DB를 새 session에서 다시 조회한다.
- 실제 모델 ID, 프롬프트 버전, 완료·실패 상태 저장을 확인한다.
- 결과를 비민감 JSON으로 출력하고 GitHub Issue 댓글에 요약한다.
- 실행별 staging DB와 volume을 폐기해 합성 데이터가 남지 않도록 한다.

## 4. 제외 범위

- 새로운 AI 기능, 프롬프트 또는 모델 정책 개발
- 기존 API 요청·응답 형식, application DB 구조 또는 상태 의미 변경
- 실제 환자·처방·대화 데이터 사용
- Production 환경 실행
- 실제 OCR 또는 CLOVA OCR 호출
- DB engine·driver·연결 URL 전환, MySQL 데이터 이관·롤백과 PostgreSQL 호환성 수정
- local·production Compose, 공통 env example, CI DB service와 Alembic 전환
- 잘못된 API Key, rate limit, 비정상적으로 짧은 timeout을 이용한 실제 장애 유도
- 공유 staging DB를 위한 실행 원장, 별도 control DB, resolver, migration lock
- 반복 실행 스케줄, 장기 결과 보관, 대시보드, 알림
- 성능, 부하, 동시성 또는 가용성 검증
- Frontend 가이드·챗봇 화면 구현
- Production 의료 안전 gate, 외부 Provider 전송 승인과 인프라 승인

위 DB 전환 항목은 Issue #67 범위다. 공유 staging DB에서 중단된 실행을 자동 복구하거나 감사 이력을 장기간 유지해야 한다면 별도 Post-MVP/Infrastructure Issue로 설계한다.

## 5. 선택한 접근 방식

### 5.1 실행별로 폐기 가능한 staging stack

host wrapper가 운영자가 지정한 UUID run ID에서 하이픈을 제거한 전체 32자리로 고유 Compose project 이름을 만든다. project를 시작하기 전에 같은 Compose project label을 가진 container·network·volume이 없는지 확인하며, 하나라도 존재하면 자동으로 합류하거나 삭제하지 않고 fixture 생성 전에 종료한다. 해당 project 안에 다음 세 service만 둔다.

- `postgres`: #67이 확정한 PostgreSQL image·healthcheck·연결 계약을 사용하는 실행 전용 application DB와 project 전용 volume
- `fastapi`: 검증 대상 immutable application image
- `release-validator`: 같은 immutable image의 one-off CLI

`fastapi`와 `release-validator`는 동일한 image RepoDigest를 사용한다. validator는 `network_mode: service:fastapi`로 실행 중 FastAPI의 `127.0.0.1:8000`을 호출하고, 합성 fixture 준비와 독립 DB 재확인을 위한 PostgreSQL staging credential만 받는다. DB host·port·driver와 healthcheck는 #67 merge 결과를 다시 정의하지 않고 그대로 따른다.

실제 `OPENAI_API_KEY`는 `.staging.env`에 저장하지 않는다. staging secret store가 wrapper process 환경에 실행 시간 동안만 주입하고 Compose가 이를 `fastapi`에만 전달한다. validator, DB, Frontend, AI Worker와 env file에는 전달하지 않는다.

wrapper는 project 충돌 검사가 끝난 뒤에만 종료 handler를 등록한다. EXIT handler는 원래 종료 코드를 보존하고 cleanup을 한 번만 실행한 뒤 같은 종료 코드로 끝낸다. INT와 TERM handler는 각각 `130`, `143`으로 종료해 EXIT handler를 거친다. cleanup 실패는 최종 결과와 종료 코드에 반영한다.

정상·실패 종료의 teardown은 실제 `.staging.env`와 OpenAI key에 의존하지 않는다. Compose file의 required interpolation을 통과시키기 위한 비밀이 아닌 고정 dummy 값과 committed `envs/example.staging.env`만 사용하고, 전체 run ID로 계산한 project label을 기준으로 폐기한다. dummy image는 pull하거나 실행하지 않는다.

```bash
OPENAI_API_KEY=teardown-not-a-real-key \
RELEASE_VALIDATION_IMAGE=invalid.local/teardown@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
POSTGRES_IMAGE=invalid.local/postgres@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
RELEASE_VALIDATION_RUN_ID="$RUN_ID" \
RELEASE_VALIDATION_REPO_DIGEST=invalid.local/teardown@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
docker compose \
  --project-name "ah-ai-smoke-${RUN_ID_COMPACT}" \
  --project-directory infra/docker \
  --env-file envs/example.staging.env \
  -f infra/docker/docker-compose.staging.yml \
  down --volumes --remove-orphans
```

SIGKILL이나 host reboot처럼 handler가 실행될 수 없는 상황에서 stack이 남으면 운영자는 project 이름을 따로 계산하지 않고 같은 wrapper의 다음 모드를 실행한다. wrapper가 normal 실행과 동일한 함수로 전체 run ID를 project 이름으로 변환하고 정확히 일치하는 Compose project만 폐기한다. 공유 DB row를 찾아 삭제하는 cleanup CLI는 만들지 않는다.

```bash
scripts/release_validation/run_ai_one_cycle_smoke.sh \
  --teardown-only \
  --run-id "00000000-0000-4000-8000-000000000001"
```

### 5.2 실행 명령

staging 검증 image는 다음 전용 build wrapper로 만든다. wrapper는 `APP_VERSION`과 `DEPLOY_COMMIT_SHA`를 build arg로 전달하고 OCI version·revision label을 확인한 뒤 push된 full RepoDigest를 출력한다.

```bash
scripts/release_validation/build_staging_image.sh \
  --repository "registry.example/ah-api" \
  --app-version "mvp-2026-08-24" \
  --commit-sha "12083f34140df8fb805f892b7601de333004adbf"
```

live smoke의 최종 사용자 인터페이스는 다음 실행 wrapper 한 개다.

```bash
scripts/release_validation/run_ai_one_cycle_smoke.sh \
  --run-id "00000000-0000-4000-8000-000000000001" \
  --image "registry.example/ah-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" \
  --app-version "mvp-2026-08-24" \
  --commit-sha "12083f34140df8fb805f892b7601de333004adbf"
```

문서의 UUID, image와 commit 값은 형식 예시이며 실제 운영 값이나 credential이 아니다. wrapper는 다음을 확인한 뒤 stack을 시작한다.

- run ID가 UUID이고 전체 32자리 compact UUID로 Compose project 이름이 계산되는가
- 같은 project label을 가진 기존 container·network·volume이 없는가
- image가 mutable tag가 아니라 full RepoDigest인가
- image의 OCI revision과 `--commit-sha`가 일치하는가
- image의 OCI version과 `--app-version`이 일치하는가
- staging DB host·이름이 Production 값과 다르고 DB 이름이 `staging_` prefix를 사용하는가
- #67이 확정한 PostgreSQL driver·service·port·migration 기준과 일치하는가
- `ENV=staging`이고 `RELEASE_VALIDATION_ALLOWED=1`인가

## 6. 구성요소

### 6.1 Host wrapper

- 입력 형식과 image provenance를 검증한다.
- 고유 Compose project를 생성한다.
- staging DB health, migration과 FastAPI readiness를 순서대로 확인한다.
- validator 실행 JSON을 임시 파일로 받는다.
- 원래 종료 코드와 signal 의미를 보존하면서 정확한 project와 volume을 한 번만 폐기한다.
- teardown 결과를 실행 JSON과 합쳐 최종 stdout JSON 한 건을 출력한다.
- API key, token, 생성 본문을 출력하지 않는다.

### 6.2 SyntheticDataFixture

validator가 staging DB에 다음 최소 데이터를 직접 준비하고 commit한다.

- run ID가 포함된 합성 사용자
- 합성 의료문서와 `COMPLETED` OCR 작업
- 처방일과 약물 한 건에 필요한 `CONFIRMED` 추출 필드
- 로그인에 사용할 합성 계정 credential

fixture는 실제 환자 정보나 실제 처방을 사용하지 않는다. root 사용자의 email·phone에는 run ID를 반영해 다른 실행과 구분한다.

### 6.3 OneCycleRunner

fixture commit 후 `httpx.AsyncClient`로 `http://127.0.0.1:8000/api/v1`에 다음 요청을 순서대로 보낸다.

1. `POST /auth/login`
2. `POST /documents/{document_id}/prescription`
3. `POST /guides`
4. `POST /prescriptions/{prescription_id}/chat-sessions`
5. `POST /chat-sessions/{session_id}/messages`

인증 token은 메모리에서만 사용한다. 각 응답의 ID를 다음 요청에 전달하고 예상 HTTP 상태를 확인한다. 현재 Backend 계약에 맞춰 처방 확정·가이드·채팅 세션·메시지 응답에서만 `Cache-Control: no-store`를 확인하며 로그인 응답에는 요구하지 않는다. 생성형 응답의 문장 전체 일치는 검사하지 않는다.

### 6.4 DatabaseVerifier

API 호출에 사용한 session과 다른 새 DB session으로 GUIDE와 ASSISTANT CHAT_MESSAGE를 조회한다.

- 생성 상태가 `COMPLETED`인가
- content가 비어 있지 않은가
- `model_name`이 허용한 `gpt-4o-mini` 계열 실제 모델 ID인가
- Guide `prompt_version == "guide-prompt-v1"`인가
- Chat `prompt_version == "chat-prompt-v1"`인가
- `error_code`와 `error_message`가 null인가
- `completed_at`이 null이 아닌가
- row가 합성 prescription과 chat session에 정확히 연결되는가

### 6.5 SmokeResult

validator는 생성 흐름과 DB 확인 결과 JSON을 wrapper에 반환한다. wrapper는 project teardown을 먼저 끝낸 뒤 `stack_teardown`과 최종 `overall`을 합쳐 stdout에 strict JSON 한 건만 출력한다. DB 기동·migration·readiness처럼 validator 실행 전에 실패했거나 validator JSON이 없거나 잘못된 경우에도 wrapper는 run ID, image provenance, 허용된 `failure_stage`, `execution=FAIL`, teardown 결과와 `overall=FAIL`을 포함한 최소 실패 JSON을 만든다. 사람이 읽는 진단은 stderr에 출력한다.

허용된 `failure_stage`는 `IMAGE_VALIDATION`, `PROJECT_COLLISION`, `STACK_START`, `DATABASE_MIGRATION`, `FASTAPI_READINESS`, `PRESCRIPTION_CONFIRMATION`, `GUIDE_GENERATION`, `CHAT_SESSION`, `CHAT_RESPONSE`, `DATABASE_VERIFICATION`, `STACK_TEARDOWN`, `RESULT_FINALIZATION`이다. 실패가 없으면 null이고 project를 만들기 전에 실패하면 `stack_teardown=NOT_RUN`이다.

```json
{
  "run_id": "00000000-0000-4000-8000-000000000001",
  "environment": "staging",
  "app_version": "mvp-2026-08-24",
  "commit_sha": "12083f34140df8fb805f892b7601de333004adbf",
  "image_repo_digest": "registry.example/ah-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "started_at": "2026-08-24T00:00:00Z",
  "finished_at": "2026-08-24T00:01:00Z",
  "execution": "PASS",
  "failure_stage": null,
  "overall": "PASS",
  "steps": {
    "prescription_confirmation": "PASS",
    "guide_generation": "PASS",
    "chat_session": "PASS",
    "chat_response": "PASS",
    "database_verification": "PASS",
    "stack_teardown": "PASS"
  },
  "guide": {
    "status": "COMPLETED",
    "model_name": "gpt-4o-mini-actual-id",
    "prompt_version": "guide-prompt-v1",
    "content_length": 120
  },
  "chat": {
    "status": "COMPLETED",
    "model_name": "gpt-4o-mini-actual-id",
    "prompt_version": "chat-prompt-v1",
    "content_length": 80
  }
}
```

실제 생성 본문, 질문 전문, access token, refresh token, API key, DB password는 포함하지 않는다.

## 7. 합성 시나리오

| 항목 | 합성 값 |
| --- | --- |
| 처방일 | `2026-08-21` |
| 약물명 | `합성의약품 에이` |
| 1회 복용량 | `1` |
| 단위 | `정` |
| 1일 복용 횟수 | `2` |
| 복용 시점 | `식후` |
| 복용 기간 | `3` |
| 챗봇 질문 | `이 합성 처방은 하루에 몇 번 복용하도록 되어 있나요?` |

OCR 인식 품질은 이번 검증 대상이 아니다. 이미 사용자가 확인한 상태를 표현하도록 추출 필드를 `CONFIRMED`로 준비한다.

## 8. 성공과 실패 판정

다음 조건을 모두 만족할 때만 Backend/AI one-cycle을 `PASS`로 판정한다.

- `execution=PASS`로 모든 HTTP 단계가 예상 상태로 완료된다.
- Guide와 Assistant message가 새 DB session에서 `COMPLETED`로 확인된다.
- 실제 모델 ID와 정확한 프롬프트 버전이 저장된다.
- 합성 prescription과 생성 결과의 소유 관계가 일치한다.
- 비민감 JSON이 생성된다.
- Compose project와 volume 폐기가 성공한다.

DB 기동, migration, readiness, API timeout, 응답 형식 오류, DB 값 불일치 또는 teardown 실패는 모두 non-zero 종료다. timeout 이후 서버 결과를 추측해 성공으로 처리하지 않는다. 해당 실행은 `FAIL`과 허용된 `failure_stage`로 기록하고 stack을 폐기한 뒤 새 run ID로 다시 실행한다.

실제 OpenAI 장애는 live 환경을 조작해 만들지 않는다. 실패 상태 저장은 결정적 mock/repository 테스트로 확인한다.

- Guide: `FAILED`, 고정 `error_code/error_message`, non-null `completed_at`, null `content/model_name/prompt_version`
- Chat: 연속된 USER와 FAILED ASSISTANT 한 쌍, 고정 오류 정보, ASSISTANT의 null `content/model_name/prompt_version`

## 9. GitHub Issue 기록

Issue 댓글에는 CLI JSON을 바탕으로 다음만 남긴다.

```markdown
## AI one-cycle 검증 결과

- 실행 시각: 2026-08-24T00:01:00Z
- 환경: staging
- App version: mvp-2026-08-24
- Commit: 12083f34140df8fb805f892b7601de333004adbf
- Image: registry.example/ah-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
- Run ID: 00000000-0000-4000-8000-000000000001
- 처방 확정: PASS
- 가이드: PASS / gpt-4o-mini 계열 / guide-prompt-v1
- 챗봇: PASS / gpt-4o-mini 계열 / chat-prompt-v1
- DB 재확인: PASS
- staging stack 폐기: PASS
- Backend/AI one-cycle: PASS
- Production 배포 승인: 아님
```

Issue 기록은 검증 증거이며 별도 운영 원장이나 장기 모니터링 시스템이 아니다.

## 10. 테스트 전략

### 10.1 결정적 자동 테스트

- staging 이외 환경과 Production DB identity 거부
- UUID run ID와 full RepoDigest 형식 확인
- full UUID project 이름과 기존 project 충돌 거부
- #67의 PostgreSQL bootstrap·`pg_isready`, application DB 설정과 FastAPI-only OpenAI key Compose render 확인
- image build arg와 OCI version·revision label 확인
- fixture commit 이후 HTTP 호출 순서와 ID 전달
- 새 DB session을 이용한 완료 상태·model·prompt 검증
- 정상 JSON, validator 이전 최소 실패 JSON과 민감정보 제외
- 정상·실패·INT·TERM 종료 코드 보존과 정확히 한 번의 project teardown
- Guide와 Chat 실패 상태 저장 전체 필드 확인

### 10.2 회귀 검사

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy app ai_worker
bash scripts/ci/run_test.sh
```

### 10.3 staging live smoke

자동 테스트와 정적 검사를 통과한 동일한 immutable image를 staging에서 한 번 실행한다. 기존 Guide·Chat 개별 live smoke는 하위 생성 모듈 확인용으로 유지하며 새 one-cycle이 대체하지 않는다.

## 11. Frontend E2E 후속 검증

현재 Frontend에는 실제 가이드·챗봇 화면과 라우트가 없으므로 Backend one-cycle에 포함하지 않는다. 화면 구현 이후 `@solia142`가 소유한 별도 Issue에서 다음 happy path 한 건을 공동 검증한다.

```text
로그인
→ 준비된 합성 처방 확정
→ 가이드 생성 및 화면 표시
→ 채팅 세션 생성
→ 질문 전송
→ 챗봇 답변 화면 표시
```

Frontend E2E는 CLI가 만든 데이터를 재사용하지 않고 자체 합성 fixture와 정리 절차를 사용한다.

## 12. 보안과 의료 안전

- 실제 환자, 처방전, 의약품 또는 대화 데이터를 사용하지 않는다.
- `OPENAI_API_KEY`는 staging FastAPI에만 배포 비밀정보로 주입한다.
- credential은 env example, Git, stdout, stderr와 Issue 댓글에 남기지 않는다.
- 질문·가이드·답변 전문을 공개 기록에 남기지 않는다.
- 실행 전용 DB와 volume은 결과 확인 후 폐기한다.
- E2E를 위한 인증 우회 또는 공개 테스트 전용 API를 추가하지 않는다.
- 사람이 생성 내용을 확인하는 의료 안전 검토는 Frontend 화면 구현 후 E2E에서 수행한다.

## 13. 담당과 완료 경계

- `@ceohwj`: 합성 시나리오, AI 안전 기준, 실제 OpenAI 검증 실행과 결과 판정
- `@phina-io`: `app/`의 validator CLI, 합성 DB fixture와 저장 결과 검증 구현·리뷰
- `@hazelnutflavoured`: staging Compose, 비밀 주입과 실행별 stack 폐기 방식 리뷰
- `@solia142`: 실제 Frontend 화면과 후속 E2E
- `@Jye-rookie`: Issue #67 안에서 PostgreSQL 전환, migration·CI와 DB 전환용 핵심 API smoke 소유

이번 P0는 #67이 확정한 PostgreSQL 연결·migration 결과를 소비할 뿐 DB 전환 파일을 다시 수정하지 않으며, 기존 사용자 API와 논리적 application DB 계약을 변경하지 않는다. 변경 필요성이 발견되면 one-cycle 구현을 중단하고 관련 CODEOWNER와 별도 계약 변경으로 분리한다.

Backend/AI one-cycle 완료 조건은 다음과 같다.

- Issue #67이 merge되고 PostgreSQL migration·전체 회귀 검사가 통과한다.
- 결정적 자동 테스트와 관련 회귀 검사가 통과한다.
- staging 실제 OpenAI one-cycle이 한 번 `PASS`한다.
- app version, commit SHA, RepoDigest, 모델 ID와 프롬프트 버전이 Issue에 기록된다.
- stack과 volume 폐기가 `PASS`한다.
- skip된 live 검사를 성공으로 간주하지 않는다.

Frontend E2E와 Production 배포 승인은 이 완료 조건에 포함하지 않는다.
