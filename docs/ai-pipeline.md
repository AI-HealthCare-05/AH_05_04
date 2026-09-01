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
- 처리: 문서 소유권 확인 → OCR 작업 생성 → 같은 요청에서 CLOVA OCR 호출 → `OCR_STRUCTURE_LLM_ENABLED=true`이면 구현된 OpenAI Structured Outputs 구조화, 아니면 규칙 기반 구조화 → 추출 필드 저장 → 결과 반환
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
- 입력: 현재 사용자 질문, 세션에 연결된 확정 약물 목록과 `history` 배열
- 처리: USER 메시지 저장 → OpenAI 단일 응답 생성 → ASSISTANT 메시지 저장을 같은 요청에서 완료
- 문맥 제한: `CHAT_HISTORY_CONTEXT_ENABLED=false`이면 history를 조회하지 않고 빈 배열을 전달한다. 비식별 합성 Local에서만 flag를 켜 같은 세션의 현재 질문 이전 완료 대화를 최대 3쌍 전달하며, 사용자·세션 식별자, 처방전 이미지와 OCR 원문·미검수 값은 전달하지 않는다.
- 안전 제한: 단일 `chat-prompt-v2`는 과거 USER 진술과 ASSISTANT 답변을 검증된 현재 사실로 취급하지 않고 현재 확정 medications를 우선한다. 추측·임의 복용 변경·확인하지 않은 인용 생성을 금지하고, 안전상 중요한 과거 정보는 현재도 해당하는지 확인하며 명시된 현재 응급·고위험 상황에서는 도움 안내를 우선한다.

이 안전 제한은 현재 프롬프트와 단위·계약 테스트의 범위입니다. [Issue #129](https://github.com/AI-HealthCare-05/AH_05_04/issues/129)은 `chat-v2-history-eval-v1` 결정론적 replay, 최대 입력의 Local application-path latency와 PII sentinel 비복제를 검증했습니다. 실제 Provider 품질·네트워크 latency·token 측정은 `NOT_RUN`이며, 합성 replay와 별도 데이터셋 결과는 Production 승인 근거가 아닙니다.

## 구현 상태 표

| 영역 | 상태 | 현재 연결 여부 | 전환 조건 |
| --- | --- | --- | --- |
| Backend 동기 OCR | MVP 구현 | FastAPI → CLOVA OCR → feature flag 기반 LLM 또는 규칙 구조화 | 현재 계약·grounding·오류 처리·검수 흐름 유지 |
| Backend 동기 가이드 | MVP 구현 | FastAPI → OpenAI | 내부 staging 검증. Production은 근거·검증 원칙 또는 코드로 강제되는 제한 모드와 재현 가능한 안전 기준 구현 후 전환 |
| Backend 동기 챗봇 | MVP 구현 | FastAPI → OpenAI. history는 기본 빈 배열이며 Local 합성 검증에서만 최대 3쌍 | 실제 대화 history 전송 승인, 질문 admission·동시성·DB 수용량과 근거·검증 안전 조건 구현 후 전환 |
| AI Worker | 골격만 존재 | 연결되지 않음 | queue 계약, consumer, 재시도·멱등성, health check와 운영 정책 구현 |
| Track E 비-RAG LLM 확장 | Approved v4 target — Partially implemented | feature flag 기반 LLM 구조화·grounding과 flag 비활성화 시 규칙 기반 경로 사용은 Current; LLM 실패 시 규칙 기반 자동 fallback은 없고 Worker 이관과 v4 provenance는 미연결 | 최소 allowlist, versioned schema·prompt·validator, raw/rule/draft/corrected/confirmed provenance와 실패 복구 구현 |
| MFDS 공식 Identity | Approved v4 target — Not implemented | 연결되지 않음 | Source Snapshot·Catalog, Candidate Resolver, Single Candidate Gate, 사용자 확인·거절, append-only Identification과 Preflight 구현 |
| Rule-first RAG·Citation | Schema-only / Approved v4 target — Not implemented | 지식 문서·청크·citation 테이블만 존재하고 실행 경로는 미연결 | 고정 LangGraph, 승인 Rule/Evidence, 결정적 Citation 완전성 검증과 fail-closed fallback 구현 |
| AI 응답 평가 | Approved v4 target — Not implemented | 자동 배포 게이트가 아니며 Evaluation Results는 `NOT_RUN` | HOLDOUT·SAFETY_REGRESSION·END_TO_END_FINAL 실행, versioned 지표·분모·신뢰구간과 승인 증빙 구현 |
| OTC 상호작용 | Track F Approved v4 target — Not implemented | 연결되지 않음 | 기존 Chat의 `OTC_INTERACTION` 유형에서 처방약–OTC Rule을 먼저 실행하고 Evidence·Citation·Safety fallback 적용 |

## Post-MVP-1 비동기 흐름 — Approved target / Not implemented

목표 흐름은 `API → AI_JOB·OUTBOX_EVENT 동일 transaction commit → Outbox publisher → Redis Stream → Worker claim/lease/fencing → Provider·RAG·검증 → 결과 commit → ACK → REST polling`이다. OCR·Guide·Chat은 공통 6개 Job 상태를 쓰고, 처방 version 변경 결과는 `STALE`로 차단한다. OCR Job 안의 비-RAG LLM 구조화는 Retrieval이나 외부 의료 Source를 호출하지 않는다. 사용자 처방 확정 뒤 Track F의 동기 Candidate·Identification·Preflight를 통과한 경우에만 Guide·Chat Job을 만들며, 근거 부족·검증 실패는 생성 답변을 폐기하고 승인 fallback 또는 공개 차단으로 처리한다.

연결 전에는 Redis consumer·retry/reclaim, Outbox reconciler, commit-before-ACK, 멱등성·소유권·STALE 계약과 장애 테스트를 구현해야 한다. `ASYNC_OCR → ASYNC_GUIDE → ASYNC_CHAT` 순으로 신규 접수만 canary 전환하며 rollback 뒤에도 기존 Job은 drain한다. 공개 기능은 [외부 승인 게이트](./release-gates/post-mvp-1-external-approvals.md)를 별도로 충족한다.

`SECURITY.md`의 근거·모델·프롬프트·검증 결과 추적은 장기 안전 원칙으로 유지합니다. 현재 MVP가 RAG·Citation·Safety를 구현하지 않았다는 상태 표시는 이 원칙을 폐기하거나 충족한 것으로 간주한다는 의미가 아닙니다. 의미 기반 NLI는 Post-MVP-1 완료 게이트에서 제외되며 결정적 Citation 완전성과 Source·Safety 검증을 먼저 적용합니다.
