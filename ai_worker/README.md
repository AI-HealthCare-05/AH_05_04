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
