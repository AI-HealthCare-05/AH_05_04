# AI 평가

의료 AI의 재현 가능한 평가 사례와 결과를 관리하는 영역입니다. 실제 환자 데이터는 포함하지 않고, 비식별 합성·공개 허용 데이터와 출처·버전을 기록합니다.

현재 저장소에는 OCR 엔진 비교·측정 자료가 `tests/evals/ocr/`에 있습니다. 아래 `evals/` 하위 영역은 Post-MVP 평가 체계의 준비 디렉터리이며, 생성·안전·RAG·OTC 평가가 현재 MVP의 자동 배포 게이트로 구현된 상태는 아닙니다.

## 영역

- `ocr/`: 약품명, 용량·단위, 횟수·복용 시점, 저신뢰 검토 요청
- `retrieval/`: Recall@K, 출처 버전, 검색시간과 선택 근거
- `generation/`: 처방 일치, Citation coverage, Faithfulness와 안전한 거절
- `safety/`: 응급·중대한 약물 위험 Recall, 금지된 복용 변경 권고
- `otc/`: 성분 매칭, 중복·상호작용 탐지, 정보 부족 Fallback

Post-MVP 평가 기능을 배포 게이트로 전환할 때는 결과에 데이터셋, 모델, 프롬프트, 검색 인덱스와 임계값 버전을 함께 기록합니다. 합의된 임계값, 재현 가능한 실행 명령과 CI 연결이 완료된 항목만 자동 배포 차단 기준으로 사용합니다.

자동 평가 체계가 아직 없다는 이유로 의료 안전 검증을 통과한 것으로 간주하지 않습니다. 현재 운영 가능 여부는 `SECURITY.md`, `docs/privacy-safety.md`와 `docs/deployment.md`의 수동 승인·차단 기준을 따릅니다.

## Chat history 평가

`generation/chat-v2-history-eval-v1.json`은 `SYNTHETIC`으로 분류된 불변 평가셋입니다. 기준선과 처리 경로 모두 `chat-prompt-v2`를 사용하며, 차이는 각각 `history=[]`와 합성 history뿐입니다. 결정론적 replay는 실제 `ChatGenerator`의 메시지 조립·검증 경로를 실행합니다.

```bash
cd backend
uv run python -m app.evaluation.chat_history_runner \
  --mode deterministic \
  --output ../evals/results/chat-v2-history-eval-v1-local-deterministic.json
```

결과에는 rule ID와 집계값만 기록하고 원시 질문·history·응답과 PII sentinel은 기록하지 않습니다. 실제 OpenAI 평가는 `RUN_OPENAI_CHAT_HISTORY_EVAL=1`, `ENV=local`, 공백이 아니고 저장소 placeholder와 일치하지 않는 `OPENAI_API_KEY`가 모두 있을 때만 `--mode live`로 실행할 수 있습니다. live 모드는 저장소의 canonical `chat-v2-history-eval-v1` 경로, `dataset_id`, `SYNTHETIC` 분류와 고정 SHA-256이 모두 일치하는 경우만 허용하며 임의 `--dataset`과 변경된 fixture를 OpenAI client 생성 전에 거부합니다. SHA-256은 Windows CRLF checkout과 LF checkout을 동일하게 취급하도록 CRLF를 LF로 정규화한 bytes에 계산하며, 줄바꿈 외 내용 변경은 계속 거부합니다. 실행하지 않은 Provider 품질·latency·token 결과는 `NOT_RUN`으로 유지하며, 결정론적 replay 결과를 실제 모델 품질이나 Production 승인 근거로 해석하지 않습니다.
