# 공통 데이터 계약

Frontend, Backend, OCR과 RAG·LLM이 공유하는 의미와 상태를 관리합니다. **현재 실행 계약**은 실제 FastAPI OpenAPI·Pydantic DTO·migration·구현과 테스트가 함께 뒷받침하는 문서입니다. `targets/post-mvp-1/`의 문서는 **Approved Contract Freeze v4의 Post-MVP-1 목표 계약**이며, RAG-00 제안은 팀 승인 전까지 `proposed/post-mvp-1/`에서 관리합니다.

상태와 승인 원본의 우선순위는 [Post-MVP-1 문서 권위](../governance/post-mvp-1-document-authority.md)를 따릅니다.

## 디렉터리 구조와 배치 원칙

계약 문서는 승인·구현 상태에 따라 다음 경로에서 관리합니다.

- `current/`: 현재 코드·OpenAPI·migration·자동 테스트가 함께 뒷받침하는 실행 계약
- `targets/post-mvp-1/`: 승인됐지만 아직 구현되지 않은 Post-MVP-1 목표 계약
- `proposed/`: 아직 구현 목표로 확정되지 않은 제안

각 계약은 하나의 정규 경로만 가지며 상태 폴더 사이에 복제하지 않습니다. 목표 계약은 관련 구현과 검증 증빙이 완료된 구현 PR에서 `current/`로 이동하고 상태를 함께 갱신합니다. Proposed 계약은 승인 결정 없이 `targets/` 또는 `current/`로 이동하지 않습니다.

## 현재 구현 계약

- [복약 가이드 Backend–AI 계약](./current/medication-guide-ai-backend.md): `guide-prompt-v3` intent·승인 문구 선택형 동기 one-cycle 입력·출력·오류 경계
- [복약 챗봇 Backend–AI Core 계약](./current/medication-chat-ai-backend.md): 현재 동기 `201` 생성과 세션 직렬화 경계
- [OCR 약품명 정규화 계약](./current/ocr-medication-normalization.md): OCR 원문, 정규화 참고값 및 사용자 확정값의 역할
- [OCR 약품 행 구조화 계약](./current/ocr-medication-structuring.md): 현재 약품 행 판정·부분 인식·사용자 확인 경계
- [처방 확정 Backend 계약](./current/prescription-confirmation.md): OCR 검수 필드로 처방을 확정할 때의 필수값, DB 경계값, Post-MVP `job_id` 검증 경계
- [회원가입·사용자 정보 계약](./current/user-account.md): 회원가입 허용 필드, 내 정보 수정 범위와 개인정보 nullable 상태
- [OCR 작업 상태 조회 계약](./current/ocr-job-status.md): OCR 작업 실패 코드와 `error_message` 노출 기준, 최신 작업 판별 기준
- [Backend 공통 오류 응답 계약](./current/backend-error-response.md): `ApiError` 사용법, 공통·도메인 오류 코드
- [Backend 공통 구현 규칙](./current/backend-common-patterns.md): 소유권 확인, 실패 상태 저장
- 공통 오류: `code`, `message`, `details`, `trace_id`

## Proposed 운영 계약 — 미구현

- [Staging Release Validation Ledger 계약](./proposed/operations/release-validation-ledger.md): staging control DB, 상태 전이, crash recovery와 migration 상호 배제

Proposed 운영 계약은 관련 schema·service·CLI·테스트가 함께 병합되고 상태가 갱신되기 전에는 실행 가능한 계약으로 간주하지 않습니다.

## RAG-00 Proposed Target — 미승인·미구현

- [Medication Candidate Search·Identification 계약 v1](./proposed/post-mvp-1/medication-candidate-identification-v1.md): 공유 DTO·후보 검색·사용자 확인·Preflight 경계
- [RAG Source 수집·정규화 계약 v1](./proposed/post-mvp-1/rag-source-ingestion-v1.md): Source 승인, 수집·검증·활성화와 Index 결속
- [RAG Runtime 계약 v1](./proposed/post-mvp-1/rag-runtime-v1.md): Guide·Chat·OTC의 Rule-first·Retrieval·Citation·Safety 공통 흐름
- [RAG Evaluation·Release Gate 계약 v1](./proposed/post-mvp-1/rag-evaluation-v1.md): RAG 전후 비교, 필수 Metric과 Release 차단 기준
- [Safety Result·Citation 계약 v2](./proposed/post-mvp-1/safety-citation-v2.md): Context STALE·다형 Citation·Release Gate

이 제안은 [RAG P0 Contract Freeze Decision 초안](../governance/decisions/2026-08-31-rag-p0-contract-freeze.md)과 함께 Issue #125 및 PR의 지정 리뷰어가 승인해야 한다. 승인 전에는 구현 기준이나 Approved v4로 사용할 수 없다. 승인 시 같은 PR에서 문서를 `targets/post-mvp-1/`로 이동하고 상태·인덱스·추적표를 `Approved Target`으로 함께 갱신한다.

## 승인된 Post-MVP-1 목표 계약 — 미구현

- [Post-MVP-1 목표 계약 인덱스](./targets/post-mvp-1/README.md)
- [비동기 Job 계약 v1](./targets/post-mvp-1/async-job-v1.md): Job 유형, 6개 상태, Chat 동시성 및 Polling
- [멱등성 계약 v1](./targets/post-mvp-1/idempotency-v1.md): 요청 지문, 중복·충돌 처리와 보존 기간
- [Transactional Outbox와 Redis Stream 계약 v1](./targets/post-mvp-1/outbox-stream-v1.md): at-least-once 전달, ACK, fencing과 메시지 경계
- [처방 버전 계약 v1](./targets/post-mvp-1/prescription-version-v1.md): 불변 snapshot, 활성화, stale 및 기존 데이터 backfill
- [Check-in과 Barrier 계약 v1](./targets/post-mvp-1/checkin-v1.md): 3개 Check-in 결과와 Barrier 명시적 거절·미제출 구분
- [OCR 비-RAG LLM 구조화 계약 v1](./targets/post-mvp-1/ocr-llm-structuring-v1.md): 최소전송, 구조화 초안 provenance, 사용자 확정과 실패 복구
- [MFDS 공식 의약품 식별 계약 v1](./targets/post-mvp-1/medication-identification-v1.md): Approved v4 선행 Target의 Source/Catalog·식별 원칙
- [Safety Result 계약 v1](./targets/post-mvp-1/safety-result-v1.md): 생성·검증·공개 상태 조합과 fail-closed 규칙

계약 파일의 존재나 문서 승인은 Worker·API·schema 구현 완료 또는 공개 승인을 의미하지 않습니다.

### RAG-00 Proposed Target 권위와 책임

RAG Source·Runtime·Evaluation·Medication Candidate·Safety/Citation v2는 외부 Manifest `post-mvp-rag-evaluation-contract@2026-08-29.8`의 Local P0 투영본이며 상태는 `PROPOSED_TARGET_NOT_IMPLEMENTED`다. 팀 승인 전에는 Approved Contract Freeze v4 또는 Current Runtime으로 해석하지 않는다.

| RAG-00 문서 영역 | 작성·변경 담당 | 책임 리뷰 |
| --- | --- | --- |
| RAG·Candidate·Citation·Evaluation | 정현우 | 권가빈 — 제품·Safety·평가 승인 |
| Backend·DB·소유권·Transaction 경계 | 정현우 | 송은영 — 공유 API·DB 계약 |
| OCR 확정 입력 경계 | 정현우 | 김지혜 — PR #96 입력 재사용·회귀 |
| Frontend 확인·상태·오류 UX 경계 | 정현우 | 남한솔 — 공유 DTO·`no-store` 소비 경계 |

실제 Issue와 PR은 구현 작성자와 책임 리뷰어를 별도로 적고 작성자의 자기 승인을 책임 리뷰로 계산하지 않는다.

### Current 승격 조건

목표 계약은 관련 코드·migration·OpenAPI/DTO, 계약·통합 테스트와 실행 증빙이 같은 구현 PR에 포함되고 관련 영역의 지정 리뷰어 승인을 받은 뒤에만 `targets/post-mvp-1/`에서 `current/`로 이동하고 Current로 표시한다. 외부 승인이나 공개 flag가 필요한 기능은 이 승격과 별도로 [외부 승인 게이트](../release-gates/post-mvp-1-external-approvals.md)를 충족해야 한다.

### Approved Contract Freeze v4에서 확정한 목표

- 비동기 Job은 `PENDING`, `PROCESSING`, `RETRY_WAIT`, `COMPLETED`, `FAILED`, `STALE`의 6개 상태를 사용한다.
- `REVIEW_REQUIRED`는 OCR 결과 검수 상태이며 Job 상태가 아니다.
- Check-in 저장 결과는 `TAKEN`, `NOT_TAKEN`, `UNCONFIRMED`의 3개다.
- Barrier의 명시적 건너뛰기·거절은 `response_status=DECLINED`, `barrier_code=null`로 표현하고, 단계에 진입하지 않거나 제출하지 않은 미제출은 응답 row를 생성하지 않는다.
- 처방 하위 결과는 불변 `prescription_version_id`에 귀속하고 최신 버전이 아니면 `STALE` 처리한다.
- AI 결과는 생성·검증·공개 결정을 분리하고 근거 부족과 검증 실패를 fail-closed 처리한다.
- OCR Job은 비-RAG LLM 구조화 초안을 만들 수 있지만 사용자 확인 전 자동 확정하지 않으며 Retrieval·외부 의료 Source 검색을 호출하지 않는다.
- 공식 제품 Resolver는 사용자 확정 `medication_name + nullable strength_text`와 활성 MFDS Catalog만 사용한다. 내부 Top-K 중 Single Candidate Gate를 통과한 최대 1개만 표시하고 사용자 확인 전 `MATCHED`로 저장하지 않는다.
- 모든 활성 처방약의 현재 Identification이 Runtime Release Bundle과 호환될 때만 Guide·Chat Job을 접수한다.
- OTC는 기존 Chat의 질문 유형이며 처방약–OTC Rule·Evidence를 먼저 실행한다. 별도 Track D API·화면·공개 flag는 두지 않는다.
- 비동기 성공 응답은 `{"data": JobStatusResponse}`, 오류는 top-level 공통 오류 envelope를 사용한다.
- 같은 Chat session에는 non-terminal Job을 하나만 허용하고 다른 키의 두 번째 요청은 `409 CHAT_JOB_IN_PROGRESS`다.
- timed occurrence는 사용자가 일정 설정 API에서 시작일·종료 결정·정확한 시각을 확인한 schedule에서만 만든다. 처방에 정확한 시각이 있어도 사용자 확인 없이 자동 생성하지 않으며, Check-in deadline은 `max(Asia/Seoul 기준 예정일 다음 날 00:00, scheduled_at + 4시간)`을 UTC instant로 snapshot한다.
- 비동기 접수의 멱등성 scope는 `(user_id, OpenAPI operation_id, key_hmac)`이고, 동기 상태 변경은 `(user_id, OpenAPI operation_id, parent_resource_id, key_hmac)` scope를 사용한다. 두 레코드 모두 원문 키의 versioned HMAC-SHA-256 결과를 `key_hmac` 컬럼에 저장한다. 둘 다 최소 24시간, 운영 기본값 7일 보존하며 비동기 동일 요청은 기존 Job의 최신 상태를 반환한다.
- 오류: `code`, `message`, `details`, `trace_id`

계약 변경은 관련 요구사항 ID, API 명세, 구현, 테스트와 함께 한 PR에서 갱신합니다. 필드 삭제·이름/타입 변경·필수 필드 추가는 Breaking Change로 취급합니다.
