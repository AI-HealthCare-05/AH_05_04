# AI Worker

## 범위

이 디렉터리는 **Post-MVP 비동기 AI Worker의 골격**입니다. 현재 MVP의 OCR, 복약 가이드와 복약 챗봇은 AI Worker를 거치지 않고 FastAPI 요청 안에서 외부 제공자를 직접 호출합니다.

현재 MVP 실행 경로는 다음 위치에 있습니다.

- OCR: `backend/app/services/ocr.py`, interface·오류 계약 `backend/app/services/ocr_engine.py`, CLOVA adapter `backend/app/services/clova_ocr_engine.py`
- 복약 가이드: `backend/app/services/guide_ai/`, `backend/app/services/guides.py`
- 복약 챗봇: `backend/app/services/chat_ai/`, `backend/app/services/chat.py`

## 현재 구현 상태

- `main.py`: placeholder 로그를 남기고 종료 코드 `0`으로 종료
- `tasks/ocr/`, `tasks/rag/`, `tasks/llm/`, `tasks/evaluation/`: package 골격만 존재하며 작업 처리 로직 없음
- 공통 재시도 여부·backoff 순수 계산 로직: 구현
- 공통 Handler·Registry·Dispatcher와 Handler 결과 식별자 검증: 구현
- 검증된 Handler 결과를 저장·commit한 뒤 ACK하도록 강제하는 추상 Consumer 실행 경계: 구현
- Redis Streams Adapter 계약과 Fake·redis-py 구현: 구현
- Consumer Group·발행·읽기·ACK·Pending·Claim: 구현 및 로컬 Redis 통합 테스트 완료
- Event Publisher의 Redis 발행·식별자 보존 경계: 구현
- SQLAlchemy Job lease 획득·heartbeat·fencing Repository와 commit-before-ACK 실행 경계: 구현
- 도메인 SQLAlchemy 결과 저장, Worker 실행 loop, reclaim·retry·quarantine·DLQ: 미구현
- Backend Outbox 선점·발행 완료 transaction과 실제 요청 경로 연결: 미구현

따라서 Compose의 `ai-worker` 서비스가 존재하거나 컨테이너가 정상 종료해도 비동기 AI 처리가 구현된 것으로 간주하지 않습니다. 로컬 Compose에서는 불필요한 재시작 루프를 막기 위해 다음 정책을 사용합니다.

```yaml
restart: "no"
```

정상 placeholder 상태는 다음과 같습니다.

```text
status=exited exit=0 restart=0
```

`infra/docker/docker-compose.prod.yml`은 현재 `restart: always`를 사용하므로 placeholder 이미지를 그대로 배포하면 종료·재시작 루프가 발생할 수 있습니다. 실제 Worker가 구현되기 전에는 Production 배포 대상에서 제외하거나 restart 정책을 별도로 확정해야 합니다.

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

### 설정

| 환경변수 | 기본값 | 의미 |
| --- | --- | --- |
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | 없음 | Redis 인증값 |
| `REDIS_STREAM_NAME` | `oryak:jobs` | 실행 Stream |
| `REDIS_CONSUMER_GROUP` | `ai-workers` | Consumer Group |
| `REDIS_CONSUMER_NAME` | `ai-worker-local` | Consumer 식별자 |
| `REDIS_BLOCK_MS` | `5000` | blocking read 시간 |
| `REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS` | `5.0` | Redis 연결 수립 timeout(초) |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `10.0` | Redis 명령 socket timeout(초) |

실제 비밀번호를 저장소·로그·이슈·문서에 기록하지 않습니다.
운영 Redis 외부 노출과 인증 설정은 별도 Infrastructure 작업의
Production 차단 조건입니다.
`REDIS_SOCKET_TIMEOUT_SECONDS`는 `REDIS_BLOCK_MS / 1000`보다 길어야 합니다.
이를 통해 정상적인 `XREADGROUP` blocking read가 socket timeout으로 먼저 중단되지 않도록 합니다.

### 생성 예시

```python
from ai_worker.adapters.factory import (
    create_redis_client,
    create_stream_adapter,
)
from ai_worker.core.config import Config

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
장기 실행 loop가 연결되기 전에는 Production 실행 경로로 활성화하지 않습니다.

## 실행과 상태 확인

로컬 Python 환경에서 placeholder 진입점을 실행합니다.

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
