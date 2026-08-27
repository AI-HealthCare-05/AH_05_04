# AI 파이프라인

## 상태 표기

- **MVP 구현**: 현재 Backend 실행 경로에 연결되어 테스트 가능한 기능
- **골격만 존재**: 디렉터리·진입점만 있고 실제 작업 처리에는 연결되지 않은 영역
- **Schema-only**: DB 모델과 migration만 존재하고 repository·service·API에는 연결되지 않은 영역
- **Approved target — Not implemented**: 계약은 승인됐지만 코드·migration·OpenAPI·자동 테스트가 아직 현재 실행 경로를 증명하지 않는 기능
- **Proposed**: 아직 승인되지 않아 구현 기준으로 사용할 수 없는 제안

## 현재 MVP 파이프라인

### OCR — MVP 구현

- 진입점: `POST /api/v1/documents/{document_id}/ocr-jobs`
- 구현: `backend/app/services/ocr.py`, interface·오류 계약 `backend/app/services/ocr_engine.py`, CLOVA adapter `backend/app/services/clova_ocr_engine.py`
- 처리: 문서 소유권 확인 → OCR 작업 생성 → 같은 요청에서 CLOVA OCR 호출 → 추출 필드 저장 → 결과 반환
- 검수: OCR 원문·정규화 참고값과 사용자 확정값을 구분하며 확정 처방에는 사용자 확정값만 사용
- 실패 처리: 제공자 timeout·장애·처리 실패를 안전한 API 오류로 변환하고 OCR 작업을 `FAILED`로 저장

사용자 수정은 OCR이 성공해 생성된 추출 필드에만 적용할 수 있습니다. 전체 OCR 실패 후 처방 필드를 새로 입력하는 수동 입력 경로는 현재 구현되어 있지 않으며, 실패한 문서는 OCR을 다시 실행해야 합니다.

현재 API의 `202 Accepted`는 비동기 queue 접수를 의미하지 않습니다. Redis나 `ai_worker/tasks/ocr/`로 작업을 전달하지 않고 FastAPI 요청 안에서 처리를 완료합니다.

### 복약 가이드 LLM — MVP 구현

- 진입점: `POST /api/v1/guides`
- 구현: `backend/app/services/guide_ai/`, `backend/app/services/guides.py`
- 입력: 사용자에게 속한 확정 처방의 약물 정보
- 처리: FastAPI가 OpenAI를 직접 호출하고 같은 요청 안에서 GUIDE 상태와 결과를 저장
- 출력 제한: 약명·용량·횟수·복용 시점·기간은 확정 처방에서 결정론적으로 렌더링하고, AI는 처방 변경이나 새로운 의료 주장을 만들지 않음

### 복약 챗봇 LLM — MVP 구현

- 진입점: `POST /api/v1/chat-sessions/{session_id}/messages`
- 구현: `backend/app/services/chat_ai/`, `backend/app/services/chat.py`
- 입력: 현재 사용자 질문과 세션에 연결된 확정 약물 목록
- 처리: USER 메시지 저장 → OpenAI 단일 응답 생성 → ASSISTANT 메시지 저장을 같은 요청에서 완료
- 문맥 제한: 이전 대화, 사용자·세션 식별자, 처방전 이미지와 OCR 원문·미검수 값은 AI에 전달하지 않음
- 안전 제한: 추측·임의 복용 변경·확인하지 않은 인용 생성을 금지하고, 정보 부족 시 확인을 요청하며 명시된 응급·고위험 상황에서는 도움 안내를 우선

이 안전 제한은 현재 프롬프트와 단위·계약 테스트의 범위입니다. 별도 데이터셋과 임계값으로 응답 품질을 판정하는 평가는 Post-MVP입니다.

## 구현 상태 표

| 영역 | 상태 | 현재 연결 여부 | 전환 조건 |
| --- | --- | --- | --- |
| Backend 동기 OCR | MVP 구현 | FastAPI → CLOVA OCR | 현재 계약·오류 처리·검수 흐름 유지 |
| Backend 동기 가이드 | MVP 구현 | FastAPI → OpenAI | 내부 staging 검증. Production은 근거·검증 원칙 또는 코드로 강제되는 제한 모드와 재현 가능한 안전 기준 구현 후 전환 |
| Backend 동기 챗봇 | MVP 구현 | FastAPI → OpenAI | 내부 staging 검증. Production은 질문 admission·동시성·DB 수용량과 근거·검증 안전 조건 구현 후 전환 |
| AI Worker | 골격만 존재 | 연결되지 않음 | queue 계약, consumer, 재시도·멱등성, health check와 운영 정책 구현 |
| RAG | Schema-only / Approved target — Not implemented | 지식 문서·청크 테이블만 존재, 검색 경로는 연결되지 않음 | 승인 지식 소스·라이선스, 인덱스, 검색 계약과 retrieval 평가 승인 |
| Citation/NLI | Schema-only / Approved target — Not implemented | 가이드·채팅 citation 테이블만 존재, 생성·검증 경로는 연결되지 않음 | 주장–출처 스키마, 검증 정책, 실패·Fallback 계약과 임계값 승인 |
| AI 응답 평가 | Approved target — Not implemented | 자동 배포 게이트 아님. 수동 의료 안전 승인은 별도 필수 | 합성·공개 데이터셋, 지표·임계값, 재현 가능한 실행과 결과 저장 구현 |
| OTC | Approved target — Not implemented | 연결되지 않음 | 성분 식별·중복·상호작용 범위와 의료 안전 검증 승인 |

## Post-MVP-1 비동기 흐름 — Approved target / Not implemented

목표 흐름은 `API → AI_JOB·OUTBOX_EVENT 동일 transaction commit → Outbox publisher → Redis Stream → Worker claim/lease/fencing → Provider·RAG·검증 → 결과 commit → ACK → REST polling`이다. OCR·Guide·Chat은 공통 6개 Job 상태를 쓰고, 처방 version 변경 결과는 `STALE`로 차단한다. Track F는 RAG·Citation·Safety Result를 분리하고 근거 부족·검증 실패를 승인 fallback 또는 공개 차단으로 처리한다.

연결 전에는 Redis consumer·retry/reclaim, Outbox reconciler, commit-before-ACK, 멱등성·소유권·STALE 계약과 장애 테스트를 구현해야 한다. `ASYNC_OCR → ASYNC_GUIDE → ASYNC_CHAT` 순으로 신규 접수만 canary 전환하며 rollback 뒤에도 기존 Job은 drain한다. 공개 기능은 [외부 승인 게이트](./release-gates/post-mvp-1-external-approvals.md)를 별도로 충족한다.

`SECURITY.md`의 근거·모델·프롬프트·검증 결과 추적은 장기 안전 원칙으로 유지합니다. 현재 MVP가 RAG·Citation/NLI를 구현하지 않았다는 상태 표시는 이 원칙을 폐기하거나 충족한 것으로 간주한다는 의미가 아닙니다.
