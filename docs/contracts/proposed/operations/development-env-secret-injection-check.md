# 개발환경·비밀정보 주입 경로 점검 운영 계약

## 상태와 범위

- 상태: **Proposed / 실구현 전 진행 기준**
- 연결 Issue: `Related #77`
- 적용 범위: Post-MVP-1 Sprint 1 착수 전 개발환경, Redis, PostgreSQL, CLOVA OCR, Provider secret, 로그·Stream·오류 응답 비밀정보 비노출 점검

이 문서는 구현 완료 보고서가 아니라, Post-MVP-1 Sprint 1 착수 전에 개발환경과 비밀정보 주입 경로의 차단 요소를 확인하고 실구현 PR에서 따라야 할 조건을 정리한 Proposed 운영 계약이다.

Redis Consumer Group과 Worker 실행 경로는 #140·#141·#233으로, reclaim·retry·quarantine·DLQ와 복구 Scheduler는 #142로 구현되었다. 다만 이 문서가 병합되더라도 Provider secret 주입·검증, Provider 실호출 기준 로그·Stream·오류 응답 비밀정보 비노출 테스트, 운영 Redis 인증·노출 차단이 완료된 것으로 간주하지 않는다. 남은 항목은 #258(실제 Provider 연결과 secret 주입), #150(운영 Redis 인증·노출 차단), health check와 Production 배포 조립에서 다루며, 관련 구현과 테스트가 병합되고 상태가 갱신되기 전에는 이 부분이 현재 실행 계약이 아니다.

실제 API key, 비밀번호, token, cookie, 환자 정보, 원본 처방전, 원본 OCR 결과는 이 문서와 Issue, PR, 로그에 기록하지 않는다.

## 목적

Post-MVP-1 Sprint 1의 비동기 기반 작업을 시작하기 전에 다음을 명확히 한다.

- Redis Consumer Group과 Stream을 실제로 사용하기 위한 설정 경계
- PostgreSQL 연결 설정과 connection pool 확인 범위
- CLOVA OCR endpoint·secret·timeout 주입 위치
- OpenAI·CLOVA secret이 저장소, CI, runner, 로그, 오류 응답에 노출되지 않는 기준
- 구현 전 gap과 운영 배포 전 차단 요소
- 후속 Issue 또는 Draft PR로 분리해야 하는 작업

## 실구현 시 필수 조건 요약

| 영역 | 실구현 시 필요한 조건 |
| --- | --- |
| Redis 운영 노출 | 운영 Redis는 기본적으로 host port에 공개하지 않는다. 공개가 필요한 경우 Redis 인증과 접근 제한을 먼저 적용한다. |
| Redis 연결 설정 | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` 사용 여부와 기본값을 Backend/Worker 기준으로 통일한다. |
| Worker 실행 | #140·#141·#233에서 Redis Consumer Group·ACK adapter·SQLAlchemy 결과 저장·lease·fencing·commit-before-ACK runtime을, #142에서 reclaim·retry·quarantine·DLQ와 복구 Scheduler를 조립했다. 실제 Provider 연결은 #258에서 완성한다. |
| Provider secret | 실제 Provider 호출이 켜진 환경에서는 CLOVA/OpenAI 설정 누락을 startup 또는 client 생성 시점에 차단한다. |
| Runner secret 경계 | release validation runner에는 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY`를 주입하지 않는다. 현재 동기 경로에서는 FastAPI에 Provider secret을 주입하고, Worker 이관 후에는 실제 Provider 호출을 담당하는 승인된 실행 서비스에만 최소 권한으로 주입한다. |
| Provider endpoint | CLOVA endpoint는 허용된 HTTPS Provider endpoint인지 검증하고, full endpoint query를 로그·오류 응답에 남기지 않는다. |
| 오류 응답 | secret, token, cookie, DB password, Provider 원문 응답, OCR 원문, 의료 원문을 응답 body에 포함하지 않는다. |
| 로그·Stream·DLQ | 일반 로그, Stream, quarantine, DLQ, 평가 artifact에 의료 원문과 인증정보를 남기지 않는다. |
| PostgreSQL pool | `pool_size`, `max_overflow`, pool wait timeout, process 수를 포함해 운영 connection 예산을 문서화한다. |
| Production 배포 | `ai-worker`는 #233의 Consumer runtime과 #142의 복구 경로를 갖췄으나, 실제 Provider 연결과 secret 주입(#258), 운영 Redis 인증·노출 차단(#150), health check와 Production 배포 조립이 완료되기 전에는 Production 처리 서비스로 배포하지 않는다. |
| 공개 차단 | 이 문서는 `PUBLIC_TRACK_C`, `PUBLIC_TRACK_F`를 새로 정의하지 않고 `docs/release-gates/post-mvp-1-external-approvals.md`의 공개 차단 기준을 따른다. |
| 문서 승격 | 구현 완료 시 실행 계약은 `current`, 운영 절차는 `deployment`, 공개 조건은 `release-gates`, 결정 이유는 ADR 또는 governance decision으로 분리한다. |

## 현재 확인된 저장소 상태

### Redis

| 항목 | 현재 상태                                                                                                                                         | 근거 |
| --- |---------------------------------------------------------------------------------------------------------------------------------------------------| --- |
| 로컬 Redis 서비스 | 있음. host port는 `"6379:6379"`로 하드코딩 | `docker-compose.yml:8-9` |
| 운영 Redis 서비스 | 있음. host port는 `"${REDIS_PORT}:6379"`로 공개 | `infra/docker/docker-compose.prod.yml:8-9` |
| Redis 인증 | 없음. `REDIS_PASSWORD`, `requirepass`, ACL 설정 없음 | 저장소 설정 검색 기준 |
| Redis 연결 env | `REDIS_PORT`는 예시 env에 있으나 `REDIS_HOST`, `REDIS_PASSWORD`는 없음 | `envs/example.local.env`, `envs/example.prod.env` |
| Redis client 코드 | AI Worker에 `redis.asyncio` client와 Stream Adapter 구현됨 (#140) | `ai_worker/adapters/redis_stream.py`, `ai_worker/adapters/factory.py` |
| AI Worker 진입점 | Consumer runtime을 조립해 Redis Stream을 소비하고 종료 신호까지 loop를 유지함 (#233) | `ai_worker/main.py`, `ai_worker/core/runtime_assembly.py`, `ai_worker/README.md:15` |
| AI Worker 공통 실행 runtime | Redis Consumer Group·ACK adapter·SQLAlchemy 결과 저장·lease·heartbeat·fencing·commit-before-ACK 경계가 조립됨 (#140·#141·#233) | `ai_worker/core/consumer_execution.py`, `ai_worker/core/runtime_assembly.py` |
| AI Worker 남은 영역 | 실제 CLOVA Provider 연결(#258), health check와 Production 배포 조립은 미완료 | `ai_worker/README.md` |

운영 compose에서 Redis를 host port로 공개하면서 인증 설정이 없으면, 외부 접근이 가능한 환경에서 `oryak:jobs` Stream에 임의 메시지를 `XADD`할 수 있다. Worker가 연결된 뒤에는 위조된 실행 요청으로 이어질 수 있으므로, 이 항목은 단순 후속 개선이 아니라 운영 배포 전 차단 요소로 본다.

### Redis Stream 목표 계약

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 실행 Stream | `oryak:jobs` | `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md:28` |
| Consumer Group | `ai-workers` | `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md:29` |
| Dead-letter Stream | `oryak:jobs:dead-letter` | `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md:30` |
| 전달 보장 | at-least-once | `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md:19` |
| Stream 금지 데이터 | 처방 내용, 약품명, 질문, 답변, OCR 텍스트, 사용자 식별정보 | `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md:48-49` |
| quarantine·DLQ 금지 데이터 | 원본 메시지나 의료정보를 저장하지 않고 digest, failure code, trace metadata 등만 저장 | `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md:51-54`, `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md:90` |

위 항목은 승인된 Post-MVP-1 목표 계약이다. #140·#141·#233으로 Redis Consumer Group과 실제 ACK adapter, SQLAlchemy 결과 저장, lease·heartbeat·fencing, commit-before-ACK runtime을 연결했고 #142로 reclaim·retry·quarantine·DLQ와 복구 Scheduler를 추가했다. 다만 실제 Provider 연결(#258), health check, Production 배포 조립과 운영 Redis 인증(#150)이 남아 있으므로 아직 Production 처리 서비스 완료를 의미하지는 않는다.

### PostgreSQL

| 항목 | 현재 상태 | 근거 |
| --- | --- | --- |
| DB env | `DB_HOST`, `DB_PORT`, `DB_EXPOSE_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` 사용 | `backend/app/core/config.py:71-80` |
| DB URL | `postgresql+asyncpg` URL 생성 | `backend/app/core/config.py:119-133` |
| connection pool size | `DB_CONNECTION_POOL_MAXSIZE` 사용 | `backend/app/core/db/databases.py:23` |
| 운영 역할별 계정 | admin, migration, app 계정 분리 env 사용 | `infra/docker/docker-compose.prod.yml:23-32` |

`DB_CONNECTION_POOL_MAXSIZE`는 명시되어 있지만 `max_overflow`와 pool wait timeout은 별도로 문서화되어 있지 않다. SQLAlchemy 기본값이 적용될 수 있으므로, 운영 배포 전 실제 process 수와 함께 connection 예산을 확인해야 한다.

### CLOVA OCR

| 항목 | 현재 상태 | 근거 |
| --- | --- | --- |
| CLOVA endpoint | `CLOVA_OCR_INVOKE_URL` | `backend/app/core/config.py:106` |
| CLOVA secret | `CLOVA_OCR_SECRET`, 기본값은 빈 문자열 | `backend/app/core/config.py:107` |
| CLOVA timeout | `CLOVA_OCR_TIMEOUT_SECONDS`, 기본값 20초 | `backend/app/core/config.py:108` |
| secret 주입 | engine 생성 시 `secret_key=config.CLOVA_OCR_SECRET` 전달 | `backend/app/dependencies/services.py:106` |
| 요청 헤더 | `X-OCR-SECRET: self._secret_key` | `backend/app/services/clova_ocr_engine.py:119-121` |

현재 CLOVA endpoint·secret·timeout은 FastAPI 동기 OCR 경로에서 소비한다. Worker 이관 전까지는 FastAPI가 실제 Provider 호출을 담당한다. Worker 이관 후에는 실제 Provider 호출을 담당하는 승인된 실행 서비스에만 최소 권한으로 Provider secret을 주입하며, release validation runner에는 주입하지 않는다. 최신 develop 기준으로도 현재 MVP의 OCR, Guide, Chat은 AI Worker를 거치지 않고 FastAPI 요청 안에서 외부 Provider를 호출한다.

`OPENAI_API_KEY`는 `sk-not-configured` placeholder를 기본값으로 사용하지만, `CLOVA_OCR_SECRET`은 빈 문자열을 기본값으로 사용한다. 이 상태에서 CLOVA OCR이 실행되면 빈 `X-OCR-SECRET` 헤더로 실제 요청을 보낼 수 있고, 설정 누락은 시작 시점이 아니라 Provider 인증 실패로 드러난다.

### CI와 secret

현재 CI workflow는 `secrets.*`를 직접 참조하지 않는다. 기본 CI는 실제 `OPENAI_API_KEY` 또는 `CLOVA_OCR_SECRET` 등록 여부와 무관하게 통과할 수 있으며, 실제 Provider 호출 테스트는 opt-in 방식으로 분리되어 있다.

이 동작은 기본 테스트를 안정적으로 유지하는 데에는 맞지만, 실제 Provider 호출을 자동화하려면 별도 secret 등록, 실행 환경 분리, secret 비노출 guard가 필요하다.

### Release validation runner

최신 develop의 release validation runner는 Provider credential이 runner 환경에 존재하지 않아야 한다는 guard를 둔다. 이 기준은 유지한다.

- runner 환경에는 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY`를 주입하지 않는다.
- 현재 동기 경로의 실제 CLOVA·OpenAI 호출은 FastAPI 실행 환경에서 수행한다.
- Worker 이관 후에는 실제 Provider 호출을 담당하는 승인된 실행 서비스에만 최소 권한으로 secret을 주입한다.
- runner는 secret 값을 읽거나 출력하지 않는다.
- runner가 수집하는 runtime environment에는 Provider credential 값을 포함하지 않는다.
- local live 검증에서 CLOVA endpoint는 허용된 HTTPS Provider endpoint인지 확인한다.

## Proposed 운영 계약

### Secret 주입 원칙

1. 실제 secret 값은 저장소, Issue, PR 본문, PR 댓글, 테스트 fixture, 로그에 기록하지 않는다.
2. 저장소에는 `example.local.env`, `example.prod.env`처럼 placeholder 또는 빈 예시만 둔다.
3. 실제 로컬 값은 `.local.env` 또는 개인 `.env`에만 둔다.
4. 운영 값은 승인된 secret 저장소에서 주입한다.
5. Provider 호출이 활성화된 실행 환경에서는 필수 secret 누락을 startup 또는 Provider client 생성 시점에 감지한다.
6. secret 누락 오류는 secret 값, endpoint query, Provider 원문 응답, stack trace를 응답 body에 포함하지 않는다.

### Redis 운영 노출 기준

운영 환경에서는 Redis를 기본적으로 host port에 공개하지 않는다. Redis는 Docker 내부 network에서 Backend, Publisher, Worker만 접근하도록 제한한다.

외부 접근이 필요한 운영 구성이 승인되는 경우에는 다음 조건을 모두 만족해야 한다.

- `REDIS_PASSWORD` 또는 ACL 기반 인증 사용
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`를 명시적으로 설정
- 인증 누락 시 Worker 또는 Publisher startup 실패
- Redis 접속 실패 시 Provider 호출이나 Job 실행을 시작하지 않음
- 비밀정보가 Redis connection error message에 포함되지 않음

현재 `infra/docker/docker-compose.prod.yml`의 `"${REDIS_PORT}:6379"` 공개 설정은 위 조건이 충족될 때까지 운영 배포 전 차단 요소로 유지한다.

### Redis Consumer Group 구현 경계

이 문서는 Redis Consumer Group 착수 전 설정 경계를 확인한 문서다. 아래 목록은 #140·#141·#233과 #142로 구현되었으며, 실제 Provider 연결은 #258에서 다룬다.

- Redis client 생성
- Consumer Group 자동 생성
- 실제 Redis Worker loop
- ACK, retry, PEL recovery
- quarantine, DLQ
- Outbox publisher
- Reconciler
- SQLAlchemy 결과 저장 adapter
- Backend API와 AI Worker 사이의 메시지 연결

### CLOVA OCR 설정 검증 기준

Provider 호출이 필요한 환경에서는 다음 설정을 빈 값으로 둘 수 없다.

- `CLOVA_OCR_INVOKE_URL`
- `CLOVA_OCR_SECRET`
- `CLOVA_OCR_TIMEOUT_SECONDS`

`CLOVA_OCR_SECRET`의 빈 문자열 기본값은 기본 CI를 통과시키기 위한 값일 수 있지만, 실제 Provider 호출 환경에서는 유효한 설정으로 취급하지 않는다.

구현 시 적용 기준은 다음과 같다.

- 실제 CLOVA OCR engine 생성 시 endpoint 또는 secret이 비어 있으면 안전한 설정 오류로 실패
- 오류 응답에는 고정 message와 비민감 details만 포함
- 로그에는 secret 값, full endpoint query, Provider 원문 응답, OCR 원문을 남기지 않음

### OpenAI 설정 검증 기준

`OPENAI_API_KEY`는 기본값이 `sk-not-configured`이므로 기본 CI에서는 필수값으로 강제하지 않는다.

다만 아래 환경에서는 placeholder를 유효한 값으로 취급하지 않는다.

- 실제 Guide 생성 Provider 호출
- 실제 Chat 생성 Provider 호출
- `OCR_STRUCTURE_LLM_ENABLED=true`인 OCR 구조화 Provider 호출

이 경우 startup 또는 Provider client 생성 시점에 설정 누락을 감지하고, 오류 응답과 로그에는 key 값을 포함하지 않는다.

### 오류 응답과 로그 비밀정보 비노출 기준

Backend 오류 응답과 로그는 다음 값을 포함하지 않는다.

- `DB_PASSWORD`
- `CLOVA_OCR_SECRET`
- `OPENAI_API_KEY`
- access token
- refresh token
- cookie
- 원본 Idempotency-Key
- OCR raw text
- confirmed medication value
- 원본 처방전 내용
- Provider 원문 응답 중 민감정보가 포함될 수 있는 본문

우선 점검 대상은 다음이다.

- `backend/app/services/ocr.py`: CLOVA 예외를 처리할 때 endpoint, Provider 원문 응답, OCR 원문이 응답이나 로그에 남지 않는지 확인
- `backend/app/core/errors.py`: `handle_http_exception`이 `str(exc.detail)`을 응답 message로 사용하므로, `HTTPException.detail`에 민감정보가 들어가는 호출 지점이 없는지 확인

Worker에는 `ai_worker/core/errors.py`의 `SAFE_MESSAGE_BY_FAILURE_CODE`를 통한 고정 문구 계약이 일부 존재한다. 후속 작업에서는 이 계약과 중복 설계하지 않고, Backend 오류 응답, Backend/Worker 로그, Stream envelope 비노출의 남은 gap을 분리해 검증한다.

### Stream envelope 비밀정보 비노출 기준

Redis Stream, quarantine, DLQ에는 실행에 필요한 식별자와 상태 전이에 필요한 비민감 metadata만 넣는다.

다음 값은 Stream, quarantine, DLQ에 저장하지 않는다.

- 처방 원문
- OCR 원문
- 사용자 질문 전문
- AI 답변 전문
- 원본 Idempotency-Key
- 인증정보
- Provider secret
- API key

Stream envelope 테스트는 Redis/Outbox 발행 골격이 구현된 뒤 추가한다.

## 실구현 PR 필수 조건

| 항목 | 현재 판정 | PR 반영 조건 |
| --- | --- | --- |
| 운영 Redis 외부 노출 제거 또는 인증 설정 | BLOCKED | #150에서 운영 compose port 공개 제거 또는 Redis 인증 설정 기준 추적 |
| Redis Consumer Group 최소 구현 | TODO | Stream/Consumer Group client 구현 Issue/PR |
| CLOVA OCR Worker 이관 | TODO | OCR 비동기 전환 Track의 별도 구현 Issue/PR |
| CLOVA 설정 누락 시작 검증 | TODO | `CLOVA_OCR_INVOKE_URL`/`CLOVA_OCR_SECRET` 검증 Issue/PR |
| OpenAI placeholder 호출 차단 | TODO | 실제 Provider 호출 경로별 설정 검증 Issue/PR |
| Release validation runner secret 차단 | TODO | runner 환경에서 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY` 존재 시 실패하는 guard 유지·검증 |
| CLOVA endpoint allowlist 검증 | TODO | 실제 Provider 호출 전 허용된 HTTPS endpoint인지 검증 |
| 오류 응답 비밀정보 비노출 테스트 | TODO | CLOVA 인증 실패·HTTPException detail 비노출 테스트 Issue/PR |
| 로그 비밀정보 비노출 테스트 | TODO | Backend/Worker log masking 또는 sentinel 검색 테스트 Issue/PR |
| Stream envelope 비노출 테스트 | TODO | Redis/Outbox 발행 골격 구현 후 payload 금지 필드 테스트 Issue/PR |
| PostgreSQL pool 예산 문서화 | TODO | process 수, `pool_size`, `max_overflow`, pool wait 기준 정리 Issue/PR |
| Production Worker 배포 차단 | TODO | 실제 Provider, health check, 운영 Redis 인증과 Production 배포 조립이 완료되기 전 `ai-worker`를 Production 처리 서비스로 활성화하지 않도록 설정·문서화 Issue/PR |
| 공개 차단 기준 연결 | TODO | `PUBLIC_TRACK_C`, `PUBLIC_TRACK_F` 해제 조건은 release-gates 문서를 따르고 이 문서에서 중복 정의하지 않음 |
| 문서 분리·승격 | TODO | 구현 완료 항목을 `current`, `deployment`, `release-gates`, ADR/governance decision으로 분리하고 proposed 문서 제거 또는 대체 처리 |

## 검증 계획

### 설정 로딩 검증

- `docker compose config`로 로컬 compose의 env interpolation 확인
- production compose에서 Redis port 공개 여부 확인
- `Config(_env_file=None)` 생성 시 기본값 확인
- `.github/workflows/checks.yml`에서 `secrets.*` 참조 여부 확인
- release validation runner 환경에 Provider credential이 남아 있으면 실패하는지 확인

### Redis 검증

- 운영 compose에서 Redis host port 공개 제거 또는 인증 설정 확인
- 인증 설정이 필요한 환경에서 `REDIS_PASSWORD` 누락 시 startup 실패 확인
- Redis connection error에 password가 노출되지 않는지 확인
- Consumer Group 생성 책임과 재실행 방법 확인

### CLOVA 검증

- `CLOVA_OCR_INVOKE_URL` 또는 `CLOVA_OCR_SECRET` 누락 시 실제 Provider 호출 전에 안전하게 실패하는지 확인
- CLOVA endpoint가 허용된 HTTPS Provider endpoint가 아니면 호출 전에 실패하는지 확인
- 실패 응답에 secret, endpoint query, Provider 원문 응답, OCR 원문이 포함되지 않는지 확인
- 로그에도 동일한 값이 남지 않는지 확인

### 오류 응답·로그 검증

고유 sentinel을 포함한 테스트 요청을 정상, 실패, 재시도 경로로 실행하고 아래 위치를 검색한다.

- 애플리케이션 로그
- Worker 로그
- 오류 응답 body
- Stream payload
- quarantine payload
- DLQ payload
- 평가 artifact

검색 결과는 0건이거나 승인된 비가역 마스킹 값이어야 한다.

### Stream envelope 검증

`docs/contracts/targets/post-mvp-1/outbox-stream-v1.md`의 금지 데이터 목록을 테스트 fixture로 만들고, Outbox 발행 직전 payload key와 Redis Stream entry에 금지 필드가 없는지 확인한다.

이 검증은 Redis/Outbox 발행 골격 구현 이후에 실행한다.

## 실구현 완료 시 문서 분리·승격 계획

이 문서는 실구현 전에는 `docs/contracts/proposed/operations/development-env-secret-injection-check.md`에 둔다. 실구현이 완료되면 이 문서 전체를 그대로 `current`로 이동하지 않고, 아래 구역별로 필요한 문서에 나누어 반영한다.

분리 기준은 다음과 같다.

- 실행 중 반드시 지켜야 하는 API·Worker·Stream 규칙은 `docs/contracts/current/`로 이동한다.
- 운영 배포자가 따라야 하는 환경·secret·Redis·pool 설정은 `docs/deployment.md`로 이동한다.
- 공개 차단과 외부 승인 조건은 `docs/release-gates/post-mvp-1-external-approvals.md`를 따른다.
- 왜 이 결정을 했는지 남겨야 하는 내용은 `docs/adr/` 또는 `docs/governance/decisions/`로 이동한다.
- 테스트 방법과 증빙 항목은 `docs/testing.md`와 `docs/testing/post-mvp-1-contract-traceability.md`로 이동한다.

아래 각 구역은 실구현 PR에서 그대로 가져가 문서화할 수 있는 기준 문구다.

### 1. `docs/contracts/current/`로 이동할 내용

#### 1.1 Provider 설정 검증 계약

실구현 완료 시 current 계약에는 다음 내용을 반영한다.

```text
Provider 호출이 활성화된 실행 경로에서는 필수 설정이 비어 있거나 placeholder이면 Provider 요청을 보내지 않는다.

CLOVA OCR 호출 경로는 `CLOVA_OCR_INVOKE_URL`, `CLOVA_OCR_SECRET`, `CLOVA_OCR_TIMEOUT_SECONDS`를 필수 설정으로 본다. `CLOVA_OCR_SECRET`의 빈 문자열은 실제 Provider 호출 환경에서 유효한 값으로 취급하지 않는다.

OpenAI 호출 경로는 실제 Guide 생성, 실제 Chat 생성, `OCR_STRUCTURE_LLM_ENABLED=true`인 OCR 구조화에서 `OPENAI_API_KEY` placeholder 값을 유효한 값으로 취급하지 않는다.

설정 누락은 startup 또는 Provider client 생성 시점에 안전한 설정 오류로 차단한다. 오류 응답에는 secret 값, endpoint query, Provider 원문 응답, stack trace, OCR 원문을 포함하지 않는다.
```

반영 후보:

- `docs/contracts/current/backend-error-response.md`
- `docs/contracts/current/ocr-job-status.md`
- `docs/contracts/current/medication-guide-ai-backend.md`
- `docs/contracts/current/medication-chat-ai-backend.md`
- OCR LLM 구조화가 current로 승격될 경우 해당 current 계약

구현 확인 기준:

- CLOVA endpoint 또는 secret 누락 시 실제 HTTP 요청이 발생하지 않는다.
- OpenAI key placeholder 상태에서 실제 Guide/Chat/OCR LLM Provider 호출이 발생하지 않는다.
- 설정 오류 응답은 공통 오류 envelope를 사용하고 민감정보를 포함하지 않는다.
- 관련 테스트가 Provider mock 또는 spy로 “호출 0건”을 검증한다.

#### 1.2 오류 응답 비밀정보 비노출 계약

실구현 완료 시 current 오류 계약에는 다음 내용을 반영한다.

```text
Backend 오류 응답은 secret, token, cookie, DB password, 원본 Idempotency-Key, Provider 원문 응답, OCR 원문, 처방 원문, 사용자 질문 전문, AI 답변 전문을 포함하지 않는다.

`HTTPException.detail`을 message로 변환하는 경로는 허용된 비민감 문구만 사용한다. Provider adapter와 service 계층은 외부 응답 원문이나 예외 chain을 사용자 응답에 그대로 전달하지 않는다.

도메인 오류는 승인된 `code`, 사용자용 `message`, 비민감 `details`, `trace_id`만 반환한다.
```

반영 후보:

- `docs/contracts/current/backend-error-response.md`
- `backend/app/core/errors.py` 관련 테스트 문서
- `docs/testing/post-mvp-1-contract-traceability.md`

구현 확인 기준:

- CLOVA 인증 실패, CLOVA timeout, OpenAI timeout, DB 오류, 알 수 없는 Provider 오류에서 응답 body에 sentinel secret이 없다.
- `handle_http_exception`으로 처리되는 400/401/403/404/405/422 경로에 민감정보가 없다.
- `rejected_value`는 개인정보·secret 가능성이 있는 필드에서 노출되지 않는다.

#### 1.3 Worker commit-before-ACK 계약

최신 develop에는 Worker 공통 실행 골격이 이미 구현되어 있으므로, 실구현 완료 시 current 계약에는 기존 구현과 Redis adapter 연결 기준을 함께 반영한다.

```text
Worker는 Handler 결과를 검증한 뒤 결과 저장과 DB commit이 성공한 경우에만 Redis Stream 메시지를 ACK한다.

Handler 실패, 결과 검증 실패, 저장 실패, commit 실패, 취소 발생 시 ACK하지 않는다. commit 이후 ACK 실패가 발생하면 이미 commit된 결과를 rollback하지 않는다.

ACK adapter는 Redis Stream message id만 ACK에 사용하고, business event id는 Job과 Outbox 중복 처리 검증에 사용한다.
```

반영 후보:

- `docs/contracts/current/`의 Worker 실행 계약 문서
- `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md`에서 current 승격되는 부분
- `docs/adr/0002-post-mvp-1-async-execution.md` 후속 갱신

구현 확인 기준:

- `ai_worker/core/consumer_execution.py`의 commit-before-ACK 순서가 Redis ACK adapter와 연결된 뒤에도 유지된다.
- commit 실패 시 ACK하지 않는 테스트가 있다.
- ACK 실패 시 rollback하지 않는 테스트가 있다.
- Handler 예외와 취소에서 rollback 후 ACK하지 않는 테스트가 있다.

#### 1.4 Stream envelope 금지 데이터 계약

실구현 완료 시 current Stream 계약에는 다음 내용을 반영한다.

```text
Redis Stream message는 실행에 필요한 식별자와 상태 전이에 필요한 비민감 metadata만 포함한다.

Stream message에는 처방 내용, 약품명, OCR 텍스트, 사용자 식별정보, 사용자 질문 전문, AI 답변 전문, 원본 Idempotency-Key, access token, refresh token, cookie, Provider secret, API key를 포함하지 않는다.

Worker는 Stream message의 `job_id`로 권한이 제한된 DB record를 조회해 작업 입력을 구성한다.

quarantine과 DLQ에는 원본 메시지나 의료정보를 저장하지 않는다. 파싱 가능한 schema/trace metadata, `stream_entry_id`, `message_digest`, `failure_code`, 수신 시각처럼 비민감 복구 정보만 저장한다.
```

반영 후보:

- `docs/contracts/current/`의 Outbox·Stream 계약
- `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md` current 승격본
- `docs/data-schema.md`의 Outbox/quarantine/DLQ 테이블 설명

구현 확인 기준:

- Stream 발행 직전 payload key에 금지 필드가 없다.
- Redis Stream entry에 금지 필드가 없다.
- quarantine row와 DLQ envelope에 원본 의료정보가 없다.
- invalid schema message에서도 원문 대신 digest와 비민감 metadata만 저장한다.

### 2. `docs/deployment.md`로 이동할 내용

#### 2.1 운영 Redis 배포 기준

실구현 완료 시 deployment 문서에는 다음 내용을 반영한다.

```text
Production Redis는 기본적으로 host port에 공개하지 않는다. Redis는 Docker 내부 network에서 Backend, Publisher, Worker만 접근한다.

운영상 host port 공개가 필요한 경우에는 Redis 인증 또는 ACL, 접근 가능한 network 제한, `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` 주입, 인증 누락 startup 실패 테스트를 먼저 충족해야 한다.

인증 없는 Redis가 외부에서 접근 가능한 상태에서는 Redis 기반 Worker를 Production에서 활성화하지 않는다.
```

반영 후보:

- `docs/deployment.md`의 Redis/Worker 배포 섹션
- `infra/docker/docker-compose.prod.yml`
- `envs/example.prod.env`

구현 확인 기준:

- `infra/docker/docker-compose.prod.yml`에서 Redis host port 공개가 제거되었거나 인증·접근 제한이 함께 적용되어 있다.
- `REDIS_PASSWORD`가 필요한 구성에서는 값 누락 시 Worker/Publisher가 시작되지 않는다.
- Redis connection error에 password가 노출되지 않는다.

#### 2.2 Provider secret 주입 기준

실구현 완료 시 deployment 문서에는 다음 내용을 반영한다.

```text
Production Provider secret은 저장소에 커밋하지 않고 승인된 secret 저장소에서 주입한다.

현재 동기 경로에서는 FastAPI 실행 환경에 실제 Provider 호출에 필요한 `CLOVA_OCR_INVOKE_URL`, `CLOVA_OCR_SECRET`, `OPENAI_API_KEY`를 주입한다.

Worker 이관 후에는 실제 Provider 호출을 담당하는 승인된 실행 서비스에만 필요한 Provider secret을 최소 권한으로 주입한다.

release validation runner에는 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY`를 주입하지 않는다. runner는 Provider credential 이름이 환경에 존재하면 실행을 거부한다.

runner는 secret 값을 읽거나 출력하지 않는다. Provider 호출 실행 위치는 현재 동기 경로에서는 FastAPI이고, Worker 이관 후에는 승인된 실행 서비스다.
```

반영 후보:

- `docs/deployment.md`
- `docs/validation/ai-one-cycle-release.md`
- `docs/designs/ceohwj/ai-one-cycle-release-validation-plan.md`
- `.github/workflows/checks.yml` 또는 배포 workflow

구현 확인 기준:

- runner 환경에 `CLOVA_OCR_SECRET` 또는 `OPENAI_API_KEY`가 있으면 guard가 실패한다.
- FastAPI에는 필요한 secret이 주입된다.
- runner runtime environment 수집 결과에 Provider credential 값이 없다.
- 실제 key 값은 로그와 artifact에 기록되지 않는다.

#### 2.3 PostgreSQL connection 예산 기준

실구현 완료 시 deployment 문서에는 다음 내용을 반영한다.

```text
Production 배포 전 PostgreSQL connection 예산을 기록한다.

기록 항목은 replica 수, Uvicorn worker 수, process별 `pool_size`, `max_overflow`, pool wait timeout, PostgreSQL `max_connections`, 운영 예비 connection 수, 비AI 요청용 예비 connection 수를 포함한다.

저장소가 명시하는 값은 `DB_CONNECTION_POOL_MAXSIZE`이며, `max_overflow`와 pool wait 정책은 실제 SQLAlchemy engine 설정 또는 배포 런타임 기준으로 확인한다.

AI 외부 호출 동안 DB transaction과 connection을 유지하는 경로가 있다면, 해당 수용량 안에서 운영 가능한지 승인 없이 배포하지 않는다.
```

반영 후보:

- `docs/deployment.md`
- `backend/app/core/db/databases.py`
- 운영 배포 기록 또는 release checklist

구현 확인 기준:

- `R × W × (pool + overflow) + 운영 예비 <= PostgreSQL max_connections` 계산이 배포 기록에 남는다.
- `SHOW lock_timeout` 결과와 단위가 기록된다.
- pool wait timeout 또는 queue 정책이 기록된다.

#### 2.4 Production Worker 배포 차단 기준

실구현 완료 시 deployment 문서에는 다음 내용을 반영한다.

```text
실제 Redis Consumer Group, ACK adapter, 결과 저장 adapter, lease·fencing은 #140·#141·#233으로, reclaim·retry·quarantine·DLQ와 복구 Scheduler는 #142로 연결되었다. 실제 Provider, health check, 운영 Redis 인증과 Production 배포 조립이 완료되기 전에는 `ai-worker`를 Production 처리 서비스로 활성화하지 않는다.

Worker image가 Production compose에 포함되는 경우 미완료 의존성으로 restart loop가 발생하지 않도록 배포 대상에서 제외하거나 restart 정책을 별도로 확정한다.

Worker를 Production에 포함하는 PR은 실제 처리 경로, health check, graceful shutdown, ACK 순서, failure handling, 비밀정보 비노출 테스트를 함께 제출한다.
```

반영 후보:

- `docs/deployment.md`
- `ai_worker/README.md`
- `infra/docker/docker-compose.prod.yml`

구현 확인 기준:

- 실제 처리 로직이 없는 `ai-worker`가 Production에서 `restart: always`로 무한 재시작하지 않는다.
- Worker health check가 실제 Redis/DB 연결 상태를 반영한다.
- Production compose에서 Worker 활성화 조건이 문서화되어 있다.

### 3. `docs/release-gates/`로 연결할 내용

#### 3.1 Privacy·공개 차단 기준

이 문서는 공개 flag를 새로 정의하지 않는다. 실구현 완료 시 release gate에는 다음 연결만 유지한다.

```text
`PUBLIC_TRACK_C`, `PUBLIC_TRACK_F` 해제 조건은 `docs/release-gates/post-mvp-1-external-approvals.md`를 따른다.

Provider secret, 로그 비노출, Stream/quarantine/DLQ 비노출, 보존·삭제 기준, EXT-PRIV-001/002 승인 증빙이 연결되기 전에는 공개 flag를 해제하지 않는다.

OTC는 Track F gate를 공유하며 별도 `PUBLIC_TRACK_D`를 만들지 않는다.
```

반영 후보:

- `docs/release-gates/post-mvp-1-external-approvals.md`
- `docs/deployment.md`의 공통 Privacy Production gate

구현 확인 기준:

- 이 문서가 `PUBLIC_TRACK_C`, `PUBLIC_TRACK_F`의 별도 의미를 중복 정의하지 않는다.
- release-gates 문서와 deployment 문서의 공개 차단 기준이 서로 충돌하지 않는다.
- EXT-PRIV-001/002 승인 전 공개 해제가 불가능하다.

### 4. `docs/adr/` 또는 `docs/governance/decisions/`로 이동할 내용

#### 4.1 Redis host port 비공개 결정

운영 Redis 정책이 확정되면 ADR 또는 governance decision에는 다음 내용을 남긴다.

```text
Decision: Production Redis는 기본적으로 host port에 공개하지 않는다.

Context: Redis가 인증 없이 공개되면 `oryak:jobs` Stream에 임의 메시지를 추가할 수 있고, Worker 연결 이후 위조된 Job 실행 요청으로 이어질 수 있다.

Decision: Redis는 내부 network에서 Backend/Publisher/Worker만 접근한다. 외부 접근이 필요한 경우 Redis 인증 또는 ACL과 network 제한을 먼저 적용한다.

Consequence: 로컬 개발 compose는 편의상 port를 열 수 있으나, 운영 compose는 내부 network 또는 인증 조건을 만족해야 한다. 운영 Redis 공개 여부는 배포 gate에서 검증한다.
```

#### 4.2 Provider secret startup 검증 결정

Provider 설정 검증 정책이 확정되면 ADR 또는 governance decision에는 다음 내용을 남긴다.

```text
Decision: 실제 Provider 호출이 활성화된 환경에서는 secret 누락을 Provider 요청 시점까지 미루지 않고 startup 또는 client 생성 시점에 차단한다.

Context: `CLOVA_OCR_SECRET`은 현재 빈 문자열 기본값을 가지며, 이 값으로 호출하면 설정 누락이 Provider 인증 실패로만 드러난다. 이는 운영 장애 원인 파악을 늦추고 빈 secret 요청을 발생시킬 수 있다.

Decision: CLOVA/OpenAI 호출이 활성화된 경로는 필수 설정을 사전에 검증한다. 기본 CI와 mock 테스트는 Provider 호출을 비활성화해 secret 없이 통과할 수 있다.

Consequence: 실제 Provider smoke나 운영 배포는 secret 주입이 없으면 시작되지 않는다. 오류 응답과 로그에는 secret 값이나 Provider 원문 응답을 남기지 않는다.
```

#### 4.3 Worker 운영 요건 완료 전 Production 제외 결정

Worker 배포 정책이 확정되면 ADR 또는 governance decision에는 다음 내용을 남긴다.

```text
Decision: 실제 Provider, health check, 운영 Redis 인증과 Production 배포 조립이 완료되기 전에는 `ai-worker`를 Production 처리 서비스로 활성화하지 않는다.

Context: Redis Consumer Group, ACK adapter, SQLAlchemy 결과 저장, lease·fencing은 #140·#141·#233으로, reclaim·retry·quarantine·DLQ와 복구 Scheduler는 #142로 구현되었다. 실제 Provider 연결과 secret 주입(#258), health check, Production 배포 조립과 운영 Redis 인증(#150)은 미구현이다.

Decision: Worker를 Production에 포함하려면 이미 연결된 Redis consumer, ACK adapter, DB 결과 저장, lease·fencing, graceful shutdown에 더해 남은 health check, Provider, 장애·재시도 테스트까지 완료한다.

Consequence: 미완료 운영 요건으로 Worker가 Production compose에서 restart loop를 만들거나 완전한 처리 서비스로 오해되지 않도록 배포 문서와 compose 설정을 함께 관리한다.
```

### 5. `docs/testing.md`와 traceability 문서로 이동할 내용

#### 5.1 설정 검증 테스트

실구현 완료 시 testing 문서에는 다음 테스트를 반영한다.

```text
설정 검증 테스트는 Provider 호출이 활성화된 환경과 비활성화된 기본 CI 환경을 분리한다.

기본 CI는 실제 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY` 없이 통과할 수 있다.

Provider 호출이 활성화된 테스트에서는 필수 설정 누락, placeholder key, 빈 secret이 실제 외부 요청 전에 차단되는지 검증한다.
```

확인할 테스트:

- `Config(_env_file=None)` 기본값 확인
- CLOVA secret 빈 문자열 차단 테스트
- OpenAI placeholder 차단 테스트
- OCR LLM 활성화 시 OpenAI key 검증 테스트
- Provider mock/spy로 외부 호출 0건 확인

#### 5.2 runner secret 차단 테스트

실구현 완료 시 testing 문서에는 다음 테스트를 반영한다.

```text
release validation runner는 Provider credential을 직접 보유하지 않는다.

runner 환경에 `CLOVA_OCR_SECRET` 또는 `OPENAI_API_KEY`가 존재하면 값 확인 없이 실행을 거부한다. 실패 메시지에는 credential 값이 아니라 credential 이름만 포함한다.
```

확인할 테스트:

- runner 환경에 `OPENAI_API_KEY`가 있으면 실패
- runner 환경에 `CLOVA_OCR_SECRET`이 있으면 실패
- 실패 메시지에 실제 값이 없음
- runtime environment 수집 결과에 Provider credential 값이 없음

#### 5.3 오류 응답·로그 sentinel 테스트

실구현 완료 시 testing 문서에는 다음 테스트를 반영한다.

```text
고유 sentinel을 secret, token, Provider 응답, OCR 원문 위치에 넣고 정상·실패·재시도 경로를 실행한다.

오류 응답 body, 애플리케이션 로그, Worker 로그, Stream payload, quarantine payload, DLQ payload, 평가 artifact에서 sentinel 원문이 발견되면 실패한다.

허용되는 값은 0건 또는 승인된 비가역 마스킹 값뿐이다.
```

확인할 테스트:

- CLOVA 인증 실패 응답 비노출
- OpenAI timeout 응답 비노출
- `HTTPException.detail` 경로 비노출
- Backend log 비노출
- Worker log 비노출
- rollback/ACK 실패 예외 chain 비노출
- Stream/quarantine/DLQ payload 비노출

#### 5.4 Redis Stream 계약 테스트

실구현 완료 시 testing 문서에는 다음 테스트를 반영한다.

```text
Outbox 발행 직전 payload와 Redis Stream entry는 허용 필드 목록만 가진다.

허용 필드는 `schema_version`, `event_id`, `event_kind`, `job_id`, `job_type`, `domain_type`, `domain_id`, `attempt`, `available_at`, `enqueued_at`, `trace_id`로 제한한다.

금지 필드는 처방 내용, 약품명, OCR 텍스트, 사용자 식별정보, 사용자 질문 전문, AI 답변 전문, 원본 Idempotency-Key, token, cookie, Provider secret, API key다.
```

확인할 테스트:

- payload key allowlist 테스트
- Redis Stream entry allowlist 테스트
- invalid schema message quarantine 테스트
- DLQ envelope 금지 데이터 테스트
- duplicate delivery에서 Provider 중복 호출 방지 테스트
- commit 전 ACK 금지 테스트

### 6. proposed 문서 제거 또는 대체 기준

실구현이 완료되면 이 proposed 문서는 계속 운영 기준의 원본으로 두지 않는다.

처리 방식은 둘 중 하나로 한다.

1. 모든 내용이 current, deployment, release-gates, ADR/governance decision, testing 문서에 반영되면 proposed 문서를 삭제한다.
2. 일부 항목이 아직 남아 있으면 proposed 문서 상단 상태를 `Superseded / 일부 항목 후속 문서로 대체`로 바꾸고, 대체 문서 목록과 남은 항목만 유지한다.

삭제 또는 대체 PR 본문에는 다음을 기록한다.

```text
이 proposed 운영 계약은 실구현 PR에서 다음 문서로 분리 반영되었습니다.

- 실행 계약: ...
- 배포 기준: ...
- 공개 차단 기준: ...
- 의사결정 근거: ...
- 테스트 증빙: ...

남은 미구현 항목이 없으므로 proposed 문서를 제거합니다.
```

## 완료 판정

이 문서 자체의 완료 조건은 다음이다.

- 저장소의 현재 설정과 코드 기준으로 Redis, PostgreSQL, CLOVA, secret 주입 경로가 기록됨
- 운영 Redis 무인증 공개가 차단 요소로 기록됨
- `REDIS_PORT`의 로컬/운영 사용 방식 차이가 기록됨
- `CLOVA_OCR_SECRET` 빈 문자열 기본값과 startup 검증 gap이 기록됨
- 오류 응답·로그·Stream 비밀정보 비노출 테스트의 후속 항목이 분리됨
- 후속 구현 항목이 실구현 PR 필수 조건 표로 분리·식별됨
- 실구현 PR에서 반드시 확인할 조건과 문서 분리·승격 기준이 기록됨

다음 조건을 만족하기 전에는 이 문서를 current 계약으로 승격하지 않는다.

- 관련 구현 병합
- migration 또는 설정 변경 병합
- API/Worker 설정 검증 테스트 병합
- 오류 응답·로그·Stream 비밀정보 비노출 테스트 병합
- 운영 배포 설정 리뷰 완료

## 참고 자료

아래 경로는 저장소 root 기준이다.

| 항목 | 상세 출처 |
| --- | --- |
| 문서 권위 규칙 | `docs/governance/post-mvp-1-document-authority.md` |
| Proposed 운영 계약 인덱스 | `docs/contracts/README.md` |
| Redis Stream 목표 계약 | `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md` |
| AI Worker 현재 구현 상태 | `ai_worker/README.md` |
| AI Worker commit-before-ACK 실행 경계 | `ai_worker/core/consumer_execution.py` |
| Privacy·보존 목표 | `docs/privacy-safety.md` |
| 배포 체크리스트 | `docs/deployment.md` |
| 외부 승인·공개 게이트 | `docs/release-gates/post-mvp-1-external-approvals.md` |
| 로컬 compose | `docker-compose.yml` |
| 운영 compose | `infra/docker/docker-compose.prod.yml` |
| 로컬 env 예시 | `envs/example.local.env` |
| 운영 env 예시 | `envs/example.prod.env` |
| Backend 설정 | `backend/app/core/config.py` |
| Backend DB engine | `backend/app/core/db/databases.py` |
| CLOVA engine 생성 | `backend/app/dependencies/services.py` |
| CLOVA OCR 요청 | `backend/app/services/clova_ocr_engine.py` |
| OCR service 오류 경계 | `backend/app/services/ocr.py` |
| Backend 오류 응답 핸들러 | `backend/app/core/errors.py` |
| AI Worker 오류 문구 | `ai_worker/core/errors.py` |
| AI Worker entrypoint | `ai_worker/main.py` |
| CI workflow | `.github/workflows/checks.yml` |
| CI 테스트 스크립트 | `scripts/ci/run_test.sh` |
