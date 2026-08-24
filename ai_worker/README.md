# AI Worker

## 범위

이 디렉터리는 **Post-MVP 비동기 AI Worker의 골격**입니다. 현재 MVP의 OCR, 복약 가이드와 복약 챗봇은 AI Worker를 거치지 않고 FastAPI 요청 안에서 외부 제공자를 직접 호출합니다.

현재 MVP 실행 경로는 다음 위치에 있습니다.

- OCR: `app/services/ocr.py`, interface·오류 계약 `app/services/ocr_engine.py`, CLOVA adapter `app/services/clova_ocr_engine.py`
- 복약 가이드: `app/services/guide_ai/`, `app/services/guides.py`
- 복약 챗봇: `app/services/chat_ai/`, `app/services/chat.py`

## 현재 구현 상태

- `main.py`: placeholder 로그를 남기고 종료 코드 `0`으로 종료
- `tasks/ocr/`, `tasks/rag/`, `tasks/llm/`, `tasks/evaluation/`: package 골격만 존재하며 작업 처리 로직 없음
- Redis consumer, 작업 dispatch, 재시도, 멱등성, health check: 미구현
- Backend API와 AI Worker 사이의 메시지 계약과 연결: 미구현

따라서 Compose의 `ai-worker` 서비스가 존재하거나 컨테이너가 정상 종료해도 비동기 AI 처리가 구현된 것으로 간주하지 않습니다. 로컬 Compose에서는 불필요한 재시작 루프를 막기 위해 다음 정책을 사용합니다.

```yaml
restart: "no"
```

정상 placeholder 상태는 다음과 같습니다.

```text
status=exited exit=0 restart=0
```

`infra/docker/docker-compose.prod.yml`은 현재 `restart: always`를 사용하므로 placeholder 이미지를 그대로 배포하면 종료·재시작 루프가 발생할 수 있습니다. 실제 Worker가 구현되기 전에는 Production 배포 대상에서 제외하거나 restart 정책을 별도로 확정해야 합니다.

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
