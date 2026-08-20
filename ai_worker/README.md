# AI Worker

OCR, 의약품 매칭, RAG, LLM 응답과 평가 작업을 비동기로 처리하는 영역입니다.

## 담당 영역

- `tasks/ocr/`: 처방전 OCR 및 저신뢰 항목 검토 요청
- `tasks/rag/`: 의료 근거 검색과 출처·인덱스 버전 기록
- `tasks/llm/`: 쉬운 설명, 안전한 Fallback과 응답 생성
- `tasks/evaluation/`: OCR·검색·생성·안전 지표 계산
- `schemas/`: API 서버와 공유하는 작업 입력·출력 계약
- `models/`: 로컬 모델 파일 안내만 추적하며 실제 가중치는 Git에서 제외
- `tests/`: 영역별 단위 테스트

API 서버와 Worker 사이의 메시지에는 작업 ID, 스키마 버전, 생성 시각,
재시도 횟수와 추적 ID를 포함합니다. 작업은 중복 전달되어도 같은 결과를 내도록
멱등성을 보장하고, 실제 처방전·환자 정보·프롬프트 원문은 로그에 남기지 않습니다.

구현 전 세부 계약은 `docs/contracts/`에 먼저 기록하고, 평가 기준은 `evals/`에서
관리합니다. 현재 `main.py`와 컨테이너 실행 명령은 초기 골격이므로 Worker 구현
이슈에서 Redis Streams consumer와 health check를 함께 완성해야 합니다.

## 실행 방법

AI Worker 컨테이너는 다음 명령을 실행합니다.

```bash
uv run --no-sync python -m ai_worker.main
```

Docker Compose에서는 다음 명령으로 실행할 수 있습니다.

```bash
docker compose up -d ai-worker
```

상태와 로그는 다음 명령으로 확인합니다.

```bash
docker compose ps -a ai-worker
docker compose logs ai-worker
```

## 현재 구현 상태

현재 AI Worker는 실행 진입점만 구성되어 있으며, 실제 queue worker와 작업 처리 로직은 아직 구현되지 않았습니다.

진입점 실행 후 종료 코드 `0`으로 정상 종료하며, 미구현 상태에서 불필요한 재시작 루프가 발생하지 않도록 다음 재시작 정책을 사용합니다.

```yaml
restart: "no"
```

정상 상태는 다음과 같습니다.

```text
status=exited exit=0 restart=0
```

재시작 횟수는 다음 명령으로 확인할 수 있습니다.

```bash
docker inspect ai-worker \
  --format 'status={{.State.Status}} exit={{.State.ExitCode}} restart={{.RestartCount}}'
```

추후 Celery 등 장기 실행 Worker가 구현되면 실제 Worker 실행 명령을 연결하고 재시작 정책을 `unless-stopped`로 변경해야 합니다.
