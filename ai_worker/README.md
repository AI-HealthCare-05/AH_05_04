# AI Worker

## 범위

이 디렉터리는 Post-MVP 비동기 AI Worker의 실행 코드와 공통 처리 경계를 포함합니다.

Worker runtime은 Redis Stream delivery를 읽고, PostgreSQL Job lease를 획득한 뒤
등록된 Handler를 실행합니다. Handler 결과는 fencing 검증을 통과한 transaction으로
저장하며, DB commit이 성공한 이후에만 Redis ACK를 수행합니다.

현재 MVP의 복약 가이드와 복약 챗봇은 아직 FastAPI 요청 안에서 외부 Provider를
직접 호출합니다. 기존 실행 경로는 다음 위치에 있습니다.

- OCR: `backend/app/services/ocr.py`, CLOVA 구현 `backend/app/services/clova_ocr_engine.py`
- 복약 가이드: `backend/app/services/guide_ai/`, `backend/app/services/guides.py`
- 복약 챗봇: `backend/app/services/chat_ai/`, `backend/app/services/chat.py`

## 현재 구현 상태

구현 완료:

- Redis Consumer Group 생성과 blocking read
- delivery 단위 동시 실행과 Worker hard timeout
- PostgreSQL Job lease 획득·heartbeat·fencing
- OCR 실행 전 `PENDING → PROCESSING` 및 `started_at`의 짧은 transaction commit
- OCR Handler, CLOVA OCR adapter 계약, 입력 조회와 결과 저장
- OCR 결과와 공통 Job 완료의 fenced transaction commit
- DB commit 이후 Redis ACK
- 종료 신호 수신, 진행 중 실행 정리와 Redis·DB resource 종료
- 실제 Redis·PostgreSQL과 명시적으로 주입한 Fake OCR Engine을 사용한
  OCR Handler 등록·dispatch one-cycle 통합 검증
- DB Outbox due row 선점·만료 claim 재선점·`WorkerMessage` 조립·Redis 발행·
  `claim_token` fencing 완료 처리 (#219)

남은 연결:

- 실제 `ClovaOcrEngine`과 규칙 기반 구조화기의 공용 패키지 분리 및
  Worker composition root 연결: #258
- CLOVA secret 주입, 공유 object storage volume, Worker 이미지 구성과
  실제 Provider smoke: #258
- Guide·Chat Handler 등록
- lease 만료 reclaim·retry·quarantine·DLQ의 runtime 연결은 #142
- Publisher 주기 실행·health check·운영 배포 조립

#233의 완료 기준은 실제 CLOVA OCR 호출이 아니라, `OcrEngine`을 주입할 수 있는
composition root와 명시적으로 주입한 Fake Engine을 사용한 Redis·PostgreSQL
one-cycle 검증이다. `ocr_engine=None`으로 OCR Handler가 등록되지 않는 실행은
#233 완료 증빙으로 사용하지 않는다.

실제 `ClovaOcrEngine`, 규칙 기반 구조화기, Provider secret, 공유 object storage와
Worker 이미지 연결 및 실제 Provider smoke는 후속 #258에서 진행한다.

## Redis Streams Adapter

Track A Worker는 Redis Client를 직접 호출하지 않고
`StreamAdapter` 계약을 사용합니다.

구현 위치:

- 계약: `ai_worker/core/stream.py`
- Fake Adapter: `ai_worker/adapters/fake_stream.py`
- Redis Adapter: `ai_worker/adapters/redis_stream.py`
- 메시지 Codec: `ai_worker/adapters/redis_message_codec.py`
- 생성 경계: `ai_worker/adapters/factory.py`
- Event Publisher: `ai_worker/core/event_publisher.py`
- Outbox Publisher: `ai_worker/core/outbox_publisher.py`
- SQLAlchemy Outbox Repository: `ai_worker/adapters/sqlalchemy_outbox_repository.py`

### 설정

| 환경변수 | 기본값 | 의미 |
| --- | --- | --- |
| `ENV` | 없음(필수) | Worker 실행 환경: `local`, `staging`, `production` |
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | 없음 | Redis 인증값 |
| `REDIS_STREAM_NAME` | `oryak:jobs` | 실행 Stream |
| `REDIS_CONSUMER_GROUP` | `ai-workers` | Consumer Group |
| `REDIS_CONSUMER_NAME` | `ai-worker-local` | Consumer 식별자 |
| `REDIS_BLOCK_MS` | `5000` | blocking read 시간 |
| `REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS` | `5.0` | Redis 연결 수립 timeout(초) |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `10.0` | Redis 명령 socket timeout(초) |
| `DB_HOST` | 없음(필수) | Worker PostgreSQL hostname |
| `DB_PORT` | `5432` | Worker PostgreSQL port |
| `DB_NAME` | 없음(필수) | Job·OCR 결과 저장 database |
| `DB_USER` | 없음(필수) | Worker runtime 전용 DB 사용자 |
| `DB_PASSWORD` | 없음(필수) | Worker runtime DB 인증값 |
| `DB_CONNECT_TIMEOUT` | `5` | PostgreSQL 연결 timeout(초) |
| `DB_CONNECTION_POOL_MAXSIZE` | `10` | Worker DB connection pool 상한 |
| `SQLALCHEMY_ECHO` | `false` | SQLAlchemy SQL 로그 출력 여부 |
| `WORKER_HARD_TIMEOUT_SECONDS` | `60.0` | delivery Handler 실행 최상위 제한 시간 |
| `WORKER_LEASE_DURATION_SECONDS` | `75.0` | Worker가 Job 실행권을 보유하는 시간 |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `10.0` | 실행 중 lease 갱신 주기 |
| `WORKER_CONCURRENCY` | `1` | 한 Worker 프로세스의 동시 delivery 처리 수 |
| `WORKER_SHUTDOWN_TIMEOUT_SECONDS` | `30.0` | 종료 시 진행 중 실행을 기다리는 상한 |
| `OCR_REQUEST_DEADLINE_SECONDS` | `60.0` | OCR Handler 전체 실행 deadline |
| `OCR_PROVIDER_BUDGET_SECONDS` | `55.0` | CLOVA 호출과 구조화를 포함한 Provider 경로 최대 예산 |
| `OCR_RESPONSE_MARGIN_SECONDS` | `5.0` | 결과 검증·저장을 위해 남겨두는 완료 여유 |

실제 비밀번호를 저장소·로그·이슈·문서에 기록하지 않습니다.
운영 Redis 외부 노출과 인증 설정은 별도 Infrastructure 작업의
Production 차단 조건입니다.
`REDIS_SOCKET_TIMEOUT_SECONDS`는 `REDIS_BLOCK_MS / 1000`보다 길어야 합니다.
이를 통해 정상적인 `XREADGROUP` blocking read가 socket timeout으로 먼저 중단되지 않도록 합니다.

### OCR Worker Handler

`job_type=OCR`, `domain_type=OCR_JOB` 메시지는 OCR Handler가 처리합니다.

- `domain_id`와 `job_id`가 모두 일치하는 `ocr_job.ai_job_id` 연결만 조회합니다.
- Provider에는 저장소 object key, MIME type, monotonic absolute deadline만 전달합니다.
- Provider 경로는 기본 55초 안에 종료하고 결과 검증·저장을 위해 5초를 남깁니다.
- OCR 원문과 Provider raw response는 Worker 결과에 포함하지 않습니다.
- Handler와 OCR Repository·ResultStore는 직접 commit하거나 Redis ACK를 수행하지 않습니다.
- OCR 결과와 공통 Job 완료는 #141 실행 계층의 fencing 검증을 통과한 동일 transaction에서 commit합니다.

기동 설정은 다음 관계를 만족해야 합니다.

```text
OCR_PROVIDER_BUDGET_SECONDS + OCR_RESPONSE_MARGIN_SECONDS
<= OCR_REQUEST_DEADLINE_SECONDS

OCR_REQUEST_DEADLINE_SECONDS
<= WORKER_HARD_TIMEOUT_SECONDS

WORKER_HARD_TIMEOUT_SECONDS + OCR_RESPONSE_MARGIN_SECONDS
<= WORKER_LEASE_DURATION_SECONDS

WORKER_HEARTBEAT_INTERVAL_SECONDS
< WORKER_LEASE_DURATION_SECONDS
```

Worker hard timeout 60초, Job lease 75초, heartbeat 10초를 기본값으로 사용합니다.
hard timeout이 발생하면 Handler task를 취소하고 해당 attempt의 결과와 ACK를 남기지
않습니다. 만료된 lease의 reclaim·retry와 최종 실패 처리는 #142가 담당합니다.

#233의 완료 기준은 실제 CLOVA OCR 호출이 아니라 `OcrEngine`을 주입할 수 있는
composition root와 명시적으로 주입한 Fake Engine 기반 one-cycle 검증입니다.
`ocr_engine=None`으로 OCR Handler가 등록되지 않는 실행은 완료 증빙으로 사용하지
않습니다. 실제 CLOVA Engine·secret·storage·Worker 이미지 연결은 후속 #258 에서 진행합니다.

### OCR 실행 transaction 경계

OCR delivery 한 건은 다음 순서로 처리합니다.

1. Redis Stream에서 delivery를 읽습니다.
2. PostgreSQL에서 Job lease와 attempt를 획득합니다.
3. 같은 시작 transaction에서 연결된 OCR 작업을 `PROCESSING`으로 전환하고
   기존 값이 없을 때만 `started_at`을 기록합니다.
4. 시작 transaction을 commit하여 다른 DB session에서도 `PROCESSING` 상태와
   lease를 관찰할 수 있게 합니다.
5. DB row lock을 유지하지 않은 상태에서 OCR Handler와 Provider를 실행합니다.
6. heartbeat로 lease를 갱신하며 fencing 유효성을 확인합니다.
7. OCR 결과와 공통 AI Job의 `COMPLETED` 상태를 별도의 fencing token 검증
   transaction에서 저장합니다.
8. 결과 transaction commit이 성공하면 Redis delivery를 ACK합니다.

.

다음 경계는 반드시 유지합니다.

- Provider 호출 전에 전에는 `PENDING → PROCESSING`과 `started_at`이 commit되어야 합니다.
- Provider 실행 중에는 `ocr_job` row write lock을 유지하지 않습니다.
- lease 또는 fencing을 잃으면 결과를 저장하거나 ACK하지 않습니다.
- hard timeout이나 Handler 실패 failure이 발생하면 해당 attempt의 성공 결과와 ACK를 남기지 않습니다.
- DB commit 전에는 Redis ACK를 수행하지 않습니다.
- 만료된 lease의 reclaim·retry와 최종 실패·quarantine·DLQ 처리는 #142 범위입니다.

### Provider observability 공용 계약

Provider context·descriptor·enum은 `provider_contracts.observability`에 있습니다.
Worker는 검증된 `WorkerMessage`와 명시적인 `DeploymentEnvironment`로 context를 만듭니다.
이 과정은 Backend 설정·DB·logger를 초기화하지 않습니다.

Worker Provider adapter와 Handler 조립은 구현되어 있으며, #233 통합 테스트에서는
Fake Engine을 명시적으로 주입해 실행 경계를 검증합니다. 실제 `ClovaOcrEngine`과
운영 observability 연결은 후속 #258 에서 진행합니다.

```python
from ai_worker.core.provider_observability import create_worker_provider_call_context
from provider_contracts.observability import DeploymentEnvironment

context = create_worker_provider_call_context(
    message=worker_message,
    environment=DeploymentEnvironment.LOCAL,
)
```

Worker Provider adapter와 Handler composition은 구현되어 있습니다.
#233 통합 테스트에서는 Fake Engine을 명시적으로 주입해 Handler 등록부터
Redis·PostgreSQL one-cycle까지 검증합니다. 실제 CLOVA Engine과 운영
observability 연결은 후속 #258 에서 진행합니다.

### 생성 예시

```python
from ai_worker.adapters.factory import (
    create_redis_client,
    create_stream_adapter,
)
from ai_worker.core.config import Config

# 프로세스 환경에 ENV=local|staging|production을 명시합니다.
config = Config()
client = create_redis_client(config)
adapter = create_stream_adapter(config, client=client)

try:
    await adapter.ensure_consumer_group()
finally:
    await client.aclose()
```

### 오류 계약

| 오류 | failure code | 의미 |
| --- | --- | --- |
| `StreamMessageEncodingError` | `INVALID_INPUT` | 8KiB 초과 또는 발행 입력 오류 |
| `StreamMessageDecodingError` | `UNSUPPORTED_SCHEMA` | 필수 필드·schema 검증 실패 |
| `StreamOperationError` | `DEPENDENCY_UNAVAILABLE` | Redis 연결·timeout·명령·ACK 실패 |

- Redis 원본 오류, Provider 응답, Secret 또는 의료정보를 안전한 오류의
메시지나 예외 chain에 포함하지 않습니다. 존재하지 않거나 이미 ACK된
entry의 XACK=0도 성공으로 처리하지 않습니다.
- 전달 보장은 at-least-once입니다. 같은 event_id가 여러 Redis entry로
발행될 수 있으며, Adapter는 event_id를 변경하지 않습니다. 결과
중복 방지는 Job·Outbox transaction과 Worker 멱등성 경계에서 처리합니다.
- 현재 Adapter 구현만으로 실제 비동기 Worker가 완성된 것은 아닙니다.
DB Outbox 발행 transaction, lease·fencing, reclaim·retry·DLQ 및 Worker
장기 실행 Consumer loop와 종료 경계는 구현되어 있습니다. 다만 기본 진입점은
`ocr_engine=None`이므로 OCR Handler를 등록하지 않습니다. 실제 CLOVA Engine,
secret과 object storage가 연결되기 전에는 Production OCR 처리 경로로
활성화하지 않습니다.
- 현재 Consumer는 필수 필드 오류나 미지원 schema entry를 batch 안에서 격리하고,
같은 batch의 정상 entry 처리를 계속합니다. 격리된 entry는 ACK하지 않고 PEL에
남기며, quarantine·DLQ 기록과 최종 ACK는 #142에서 구현합니다.

## 실행과 상태 확인

로컬 Python 환경에서 Worker runtime 진입점을 실행합니다.
기본 진입점은 `ocr_engine=None`으로 실행되므로 OCR Handler를 등록하지 않습니다.
이 실행은 resource 생성·종료 경계 확인용이며 #233의 OCR 완료 증빙으로 사용하지 않습니다.
OCR one-cycle은 테스트에서 Fake Engine을 명시적으로 주입해 검증합니다.

```bash
uv run python -m ai_worker.main
```

Docker Compose 서비스명은 `ai-worker`입니다.

```bash
docker compose up -d --build ai-worker
docker compose ps -a ai-worker
docker compose logs ai-worker
```

재시작 횟수는 다음 명령으로 확인할 수 있습니다.

```bash
docker inspect ai-worker \
  --format 'status={{.State.Status}} exit={{.State.ExitCode}} restart={{.RestartCount}}'
```

## Post-MVP 전환 조건

AI Worker를 실제 요청 경로에 연결하기 전에 다음 조건을 모두 충족해야 합니다.

1. `docs/contracts/`에 작업 ID, schema version, 생성 시각, 재시도 횟수, trace ID를 포함한 입력·출력 계약을 기록합니다.
2. API 접수·조회 상태, 오류 의미, timeout, 취소와 재시도 정책을 합의합니다.
3. Redis consumer와 필요한 OCR·RAG·LLM·평가 작업을 구현합니다.
4. 중복 전달에도 같은 결과를 내는 멱등성과 실패 복구를 구현합니다.
5. 실제 처방전·환자 정보·프롬프트 원문을 로그에 남기지 않고 외부 전송·보존 정책 승인을 받습니다.
6. health check, graceful shutdown, contract·integration·장애·재시도 테스트를 추가합니다.
7. 장기 실행 Worker에 맞는 실행 명령과 배포 환경의 restart 정책을 검증합니다.

RAG, Citation/NLI 검증, AI 응답 평가와 OTC 기능은 Worker 자체와 별개의 Post-MVP 기능입니다. 각 기능의 지식 소스, 라이선스, 스키마, 평가 데이터셋·지표·임계값이 승인된 뒤 해당 task를 구현합니다.
