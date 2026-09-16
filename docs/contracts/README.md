# 공통 데이터 계약

- [OCR LLM 동의·전송 후속 (#458)](proposed/ocr-llm-transfer-458.md): #505에서 OCR 목적 Backend API·접수 Gate, Worker 재검사, Frontend 연결과 안전한 LLM 생략을 구현. 최종 안내 문구·policy version, 검증된 최소 전송 selector와 실제 사용자 대상 LLM 활성화는 미완료.
- [OCR 동의 안내·정책 버전 검토안 (#458)](proposed/ocr-consent-notice-policy-review-458.md): 데모 OCR 안내와 향후 LLM 안내, 기존 동의자의 재동의 및 버전 결정 질문. 승인된 운영 문구·버전은 아님.

Frontend, Backend, OCR과 RAG·LLM이 공유하는 의미와 상태를 관리합니다. **현재 실행 계약**은 실제 FastAPI OpenAPI·Pydantic DTO·migration·구현과 테스트가 함께 뒷받침하는 문서입니다. `targets/post-mvp-1/`의 문서는 승인된 Post-MVP-1 목표 계약이며 문서별 구현 상태를 별도로 표시하고 Approved Contract Freeze v4와 RAG-00 Approved Target을 함께 관리합니다.

상태와 승인 원본의 우선순위는 [Post-MVP-1 문서 권위](../governance/post-mvp-1-document-authority.md)를 따릅니다.

- [Source Attempt Receipt 실패 보완 (#436)](./proposed/source-attempt-receipt-436.md): COLLECTION_FAILED 및 EMPTY_RESULT 계열 구분 구현 리뷰안.

- [공통 복약 리포트 v1 (#419)](./current/medication-report-v1.md): KST 7/30일·과거 version·두 지표·0분모·상세 기록. Backend #478·Frontend #574 병합 및 실제 runtime 검증.

- [Track B 일정 API v1 / #628 최신 처방 범위](./proposed/track-b-schedule-api-v1.md): SELF 최신 처방 한 건으로 schedule_items 정합화; 요청자 범위 확인, 구현 리뷰 대기. 과거 occurrence 이력 보존.

## 디렉터리 구조와 배치 원칙

- [PD-398-M1 Source·Catalog 관리](./proposed/source-catalog-management-398.md): 분리된 관리 API, 서버 권한, 미사용 자료 수정·삭제와 감사 transaction, PD-398-R2 Snapshot 잠금 권한 분리, #372 Alias review_status 승인 보호 연동.
- [PD-398 Python Candidate 결과 저장](./proposed/python-candidate-integrity-398.md): 결과 저장·최종화 원자성, 최소 권한, 기존 Trigger 제거 구현.
- [PD-398 Python Prescription 무결성](./proposed/python-prescription-integrity-398.md): count/hash 저장·소비 검증·NOT NULL·최소 권한·기존 Trigger 제거, 확정·정정 성공 응답 멱등 재현.
- [PD-398 Python append-only 이력](./proposed/python-append-only-integrity-398.md): Runtime 전이·Check-in·Evidence 이력의 Python transaction과 INSERT 전용 권한.
- [PD-398 Python Snapshot 상태 전이](./proposed/python-snapshot-transition-398.md): 작업 브랜치 구현·로컬 검증 완료. Source 제거 migration·Writer·관리 경로·검증된 Snapshot의 일반 제약 삭제 방어는 연결됐으며 담당 리뷰·운영 적용은 별도.
- [PD-398 Python Catalog 무결성](./proposed/python-catalog-integrity-398.md): Draft PR #372의 Identity·Search Entry·Catalog Set 함수 5개와 Trigger 15개를 Python 저장 경계로 전환하는 병합 기준.

계약 문서는 승인·구현 상태에 따라 다음 경로에서 관리합니다.

- `current/`: 현재 코드·OpenAPI·migration·자동 테스트가 함께 뒷받침하는 실행 계약
- `targets/post-mvp-1/`: 승인된 Post-MVP-1 목표 계약(문서별 구현 상태 표시)
- `proposed/`: 아직 구현 목표로 확정되지 않은 제안

각 계약은 하나의 정규 경로만 가지며 상태 폴더 사이에 복제하지 않습니다. 목표 계약은 관련 구현과 검증 증빙이 완료된 구현 PR에서 `current/`로 이동하고 상태를 함께 갱신합니다. Proposed 계약은 승인 결정 없이 `targets/` 또는 `current/`로 이동하지 않습니다.

## 현재 구현 계약

- [공통 복약 리포트 v1](./current/medication-report-v1.md): `GET /api/v1/medication-reports`의 7/30일 SELF 집계·상태·두 비율·상세 기록 계약
- [복약 가이드 Backend–AI 계약](./current/medication-guide-ai-backend.md): `guide-prompt-v3` intent·승인 문구 선택형 동기 one-cycle 입력·출력·오류 경계
- [복약 챗봇 Backend–AI Core 계약](./current/medication-chat-ai-backend.md): 현재 동기 `201` 생성과 세션 직렬화 경계, v5 짧은 후속 질문 프롬프트 지침
- [OCR 약품명 정규화 계약](./current/ocr-medication-normalization.md): OCR 원문, 정규화 참고값 및 사용자 확정값의 역할
- [OCR Provider 약품명 필드 Alias 예방 계약](./current/ocr-provider-field-aliases.md): 현재 외부 alias가 없는 상태에서 `medication_name`·`MEDICATION_NAME` 정본과 향후 Source별 Provider Adapter 변환 경계를 고정
- [OCR 약품 행 구조화 계약](./current/ocr-medication-structuring.md): #144 빈 검수 필드 개정은 작업 브랜치 검증 완료·리뷰/병합 대기. 현재 약품 행 판정·부분 인식·사용자 확인 경계
- [처방 확정 Backend 계약](./current/prescription-confirmation.md): OCR 검수 필드로 처방을 확정할 때의 필수값, DB 경계값, Post-MVP `job_id` 검증 경계
- [회원가입·사용자 정보 계약](./current/user-account.md): 회원가입 허용 필드, 내 정보 수정 범위, 개인정보 nullable 상태, 목적별 동의 상태 API, 현재 구현된 `token_version` 기반 인증 세션 무효화
- [OCR 작업 상태 조회 계약](./current/ocr-job-status.md): OCR 작업 실패 코드와 `error_message` 노출 기준, 최신 작업 판별 기준
- [공통 Job 상태 조회 계약 v1](./current/job-status-v1.md): `GET /api/v1/jobs/{job_id}` 응답 필드, 6개 Job 상태 의미, 소유권 이중 확인, 오류 계약
- [Backend 공통 오류 응답 계약](./current/backend-error-response.md): `ApiError` 사용법, 공통·도메인 오류 코드
- [Local Live Provider 호출 증적 계약](./current/live-provider-call-evidence.md): `local-live-full` 요청 상관관계, Provider JSONL과 수동 증빙 판정
- [Backend 공통 구현 규칙](./current/backend-common-patterns.md): 소유권 확인, 실패 상태 저장
- [PROFILE SELF 소유권 전환 계약 v1](./current/profile-self-ownership-v1.md): 본인 단일 SELF profile과 `profile_id` 기반 사용자 리소스 소유권 기준
- [Track B Notification 계약 v1 (#203)](./current/track-b-notifications-v1.md): 알림 저장·목록·읽음·재알림, 원본 `occurrence_local_date`, 멱등성과 Check-in 분리 경계
- [Track B occurrence 원래 약 표시 조회 v1 (#202)](./current/track-b-occurrence-medication-v1.md): occurrence의 불변 version medication 조회, SELF 404와 current medication fallback 금지
- [Track C ActionPlan 조회·완료·취소 v1 (#617)](./current/track-c-plan-lifecycle-617.md): PR #618의 단건 GET·단일 종료 PATCH·멱등성·SELF 계약. 권가빈 구현·김지혜 승인 후 PR #618 병합(`a542bcc2`), 외부 공개 게이트 별도.
- 공통 오류: `code`, `message`, `details`, `trace_id`

## Proposed 계약

- [Guide·Chat 피드백 v1 (#633)](./proposed/guide-chat-feedback-v1.md): 완료 결과별 rating·선택 의견 저장 API, SELF 소유권·재제출·합성 Gold 연결 검토 초안. Proposed / Local 구현·검증 중, Frontend·Backend 확인과 사용자 운영안 채택 반영; 실사용 처리 승인·최종 책임 리뷰 별도.

- [Track B 생활 시간 입력 v1 (#422 / #556)](./proposed/track-b-lifestyle-times-v1.md): 식사·반복 행동·복용 곤란 시간의 SELF별 요일 저장·조회 구현 후보. #556 책임 리뷰 전 Proposed; 추천·약별 조건 판정은 별도.

- [Knowledge Evidence Index v1 (#178 선행 기반)](./proposed/post-mvp-1/knowledge-evidence-index-v1.md): 승인된 RAG Runtime 목표의 Retrieval Adapter가 소비할 Source Snapshot 결속 Chunk·버전별 embedding·재현 가능한 receipt 저장 계약. 구현 브랜치 검증 중이며 #178 Retrieval/RRF/Rerank/Evidence Gate·공개 활성화는 포함하지 않음.
- [Knowledge Evidence Search 및 Deterministic RRF v1 (#178)](./proposed/post-mvp-1/knowledge-evidence-search-rrf-v1.md): 승인된 Knowledge Evidence Index 대상 PostgreSQL Lexical(Exact/Trigram/FTS)·Dense(pgvector Cosine) 검색 및 결정적 RRF(`rrf-rank-fusion@1`) 융합 계약. 구현 브랜치 검증 중이며 Reranker/Evidence Gate/authoritative Retrieval Run/Runtime graph 연결·공개 활성화는 포함하지 않음.
- [Retrieval Run 및 Evidence Gate Runtime Core v1 (#178)](./proposed/post-mvp-1/retrieval-run-v1.md): Issue #178 Retrieval Run/Signal/Hit 원자적 persistence, Production Evidence Gate pre/post 검증 및 hybrid_retrieve runtime core 계약. 구현 브랜치 검증 중이며 RET-HR/reranker·actual evaluation·공개 활성화는 포함하지 않음.
- [Guide Evidence Handoff Contract Kernel v1 (#180 선행 계약)](./proposed/post-mvp-1/guide-evidence-handoff-v1.md): #174 REQUEST Guard의 Source/Member 결정 관측 결과와 #178 Production Retrieval selections 사이의 exact-match 결속, JCS 정규 해시 프로젝션, Two-input 재검증 순수 계약 커널. 순수 계약 커널 구현 브랜치 검토 중; authority 발급·영속화·런타임 통합은 미구현.

- [OCR LLM Worker 범위 정정 (#453)](./proposed/ocr-llm-worker-consent-453.md): 기존 이관 범위와 리뷰 시 별도 검토할 항목. 기존 동의 개정안 미채택.
- [목적별 동의 Gate 계약 제안 (PD-207)](./proposed/consent-gate-207.md): OCR/GUIDE/CHAT/NOTIFICATION 목적별 GRANTED/WITHDRAWN 동의 상태와 row 없음=미동의 기준. #465에서 `user_consent` 저장 기반을 병합했고, #510에서 현재 사용자 목적별 동의 상태 조회·변경 API를 추가했다. #505는 OCR 목적의 Backend 동의 API·접수 Gate, Worker 재검사·차단 저장과 Frontend 소비를 구현했다. GUIDE/CHAT/NOTIFICATION 실행 연결, OCR 최종 정책 문구·버전과 Production 공개 승인은 후속 범위다. 전체 목적별 계약은 Proposed 유지.

- [Track B UNCONFIRMED backlog v1](./proposed/unconfirmed-backlog-v1.md): PD-418 URL·cursor·DTO·오류. #426 후보와 #462 실제 등록·PUT 보완·페이지 이동·날짜별 revision 검증 병합 완료; #462 남한솔 APPROVED·최종 CI 7/7 확인. 문서는 Proposed 유지, Current 승격 별도 검토. [승인 범위·상태 근거](../governance/decisions/2026-09-10-unconfirmed-backlog-418.md#승인-증빙과-등록-변경의-병합-조건).

- [Source Artifact·REJECTS 보존·삭제 정책 초안 (#335)](./proposed/post-mvp-1/source-artifact-retention-cleanup.md): PM 30일 유예·참조 보존·수동 배치 승인 반영, 통합 검토 대상, 후속 구현 [#347](https://github.com/AI-HealthCare-05/AH_05_04/issues/347)·김지혜 담당. Local 합성 #347의 승인 순서·revision·경합 잠금·DB 감사 근거·참조 범위 보완 연결 포함. 운영 삭제·활성화 승인 아님.

- [Protected HOLDOUT Dataset 폐기 감사 계약 (#425)](./proposed/post-mvp-1/protected-holdout-disposal-audit.md): PD-368 §8이 분리를 지시한 폐기 감사 계약. 기존 audit chain에 `DISPOSAL` variant를 두고 `INTENT`→`SUCCEEDED`/`UNKNOWN`→재조정을 규정한다. 운영 종료는 기존 `DatasetStatus.RETIRED`를 재사용하며 새 상태를 만들지 않는다. 폐기 후 재현 범위 정책 결정 전까지 **실제 폐기는 차단 유지**. 실행 승인 아님.

- [Staging Release Validation Ledger 계약](./proposed/operations/release-validation-ledger.md): staging control DB, 상태 전이, crash recovery와 migration 상호 배제
- [개발환경·비밀정보 주입 경로 점검 운영 계약](./proposed/operations/development-env-secret-injection-check.md): Redis, PostgreSQL, Provider secret 주입 경로와 운영 배포 전 차단 조건
- [Track A migration·rollback 계획 제안 v1](./proposed/track-a-migration-rollback-v1.md): 문서 상태 Proposed · 구현 상태 Partially implemented — 공통 Job 기반과 OCR–AI Job mapping을 구현했으며 Guide·Chat 연결, Prescription Version, 전체 비동기 전환·backfill·read cutover는 미구현
- [계정 생명주기 후속 계약 v1 (`PD-206`)](./proposed/account-lifecycle-v1.md): 문서 상태 Proposed · 구현 상태 Partially implemented — 회원탈퇴의 transaction 경계와 후속 구현 기준. 로그아웃·`token_version` 재검증·refresh token rotation·비밀번호 재설정은 현재 구현 계약([`user-account.md`](./current/user-account.md))에 반영됨. `account_deletion_request` 저장 기반(5절)은 작업 브랜치 구현·로컬 검증 완료; 탈퇴 요청 접수 API(4절)와 삭제·보존 처리는 미구현
- [Guide·Chat Session·Message 상태 구현 골격 v1](./proposed/guide-chat-session-message-status-ui-v1.md): Session/Message/Job 결과 상태축, SAFETY-STALE 경계, PROFILE 기반 소유권의 Frontend 구현 골격

Proposed 계약은 문서별 구현 상태를 별도로 표시합니다. 부분 구현은 전체 계약 완료나 Current 승격을 의미하지 않으며, 관련 schema·service·CLI·테스트와 남은 전환 단계가 완료되고 상태가 갱신되기 전에는 실행 가능한 전체 계약으로 간주하지 않습니다.

## 승인된 Post-MVP-1 목표 계약

- [Track B 일정 정합화 v1 (#417)](./targets/post-mvp-1/track-b-schedule-reconciliation-v1.md): PR #424 승인 목표. DB #438·알림 #430·일정 API #456 병합; Current 승격과 과거 약 표시 경로의 검토는 별도.

- [Post-MVP-1 목표 계약 인덱스](./targets/post-mvp-1/README.md)
- [비동기 Job 계약 v1](./targets/post-mvp-1/async-job-v1.md): Job 유형, 6개 상태, Chat 동시성 및 Polling — Job 상태 조회 GET과 OCR 접수 POST 구현 완료(#148) · rediscovery GET은 서비스 로직 구현·라우트 등록 보류 · Guide/Chat 접수 POST와 Reconciler 미구현
- [멱등성 계약 v1](./targets/post-mvp-1/idempotency-v1.md): 요청 지문, 중복·충돌 처리와 보존 기간
- [Transactional Outbox와 Redis Stream 계약 v1](./targets/post-mvp-1/outbox-stream-v1.md): at-least-once 전달, ACK, fencing과 메시지 경계
- [처방 버전 계약 v1](./targets/post-mvp-1/prescription-version-v1.md): 불변 snapshot, 활성화, stale 및 기존 데이터 backfill
- [Check-in과 Barrier 계약 v1](./targets/post-mvp-1/checkin-v1.md): 3개 Check-in 결과와 Barrier 명시적 거절·미제출 구분; #413 Check-in PUT·#456 일정 API 병합, 전체 목표 Current 승격은 별도, [HTTP 구체화 제안](../governance/decisions/2026-09-10-checkin-api-202.md)
- [OCR 비-RAG LLM 구조화 계약 v1](./targets/post-mvp-1/ocr-llm-structuring-v1.md): 최소전송, 구조화 초안 provenance, 사용자 확정과 실패 복구
- [MFDS 공식 의약품 식별·Candidate 계약 v1](./targets/post-mvp-1/medication-identification-v1.md): 공식 Source/Catalog·후보 검색·사용자 확인·Preflight 공유 경계. #583에서 Candidate Index `BUILDING→READY/FAILED`, 기존 READY `RETIRED`, code당 active READY 1개, `lexical_storage_hash`/`embedding_storage_hash` 기반 READY 전 재검증과 legacy receipt fail-closed 의미를 목표 정본에 반영했다. Runtime 활성화와 공개 전환은 후속 범위다.
- [Safety Result 계약 v1](./targets/post-mvp-1/safety-result-v1.md): Approved v4 이력과 Track C 공통 Safety 기준; Track F 후속 의미는 v2가 대체
- [RAG Source 수집·활성화 계약 v1](./targets/post-mvp-1/rag-source-ingestion-v1.md): Source 승인, 수집·검증·활성화와 Index 결속 · MFDS 제품 `mfds-product-approval@1` canonicalization과 `ProductIngestionResult` 경계, 실패 재시도 충돌·Verification 불변성·REJECTS 1:1·DB-owned 상태 전이 검증 구현 중(#165), 비FAILED 계보 유지·#335/#347 보존·정리 완료·#362 정책/외부 version/시도 provenance 영속화 구현 및 #165 잔여 allowlist 계약 추적 #436은 invalid Version의 저장 진입 감사와 명시적인 Attempt decision 복원을 포함한다.
- [RAG Runtime 계약 v1](./targets/post-mvp-1/rag-runtime-v1.md): Guide·Chat·OTC의 Rule-first·Retrieval·Citation·Safety 공통 흐름, Runtime Bundle의 Manifest Hash·저장 정합 계약(`PD-175-20260910` Approved)
- [Guideline Card typed port 계약 v1](./targets/post-mvp-1/guideline-card-v1.md): RAG-14 Evidence Gate·PD-362 Source eligibility와 RAG-16 사이의 Request·Outcome·승인 verifier·fallback 계약 — RAG-15 persistence-free kernel 구현 검토 중(#179, PR #414), Current 아님
- [RAG Evaluation·Release Gate 계약 v1](./targets/post-mvp-1/rag-evaluation-v1.md): RAG 전후 비교, 필수 Metric, Schema Set 1.4 `Candidate · Review Required` Grounding/Safety projection 계약과 Release 차단 기준
- [RAG Answer Quality Metric·Variant 계약 v1 (#159)](./targets/post-mvp-1/rag-answer-quality-metrics-v1.md): PR #475 책임 리뷰 승인으로 확정된 Approved Target. `REQUIRED_CLAIM_RECALL`·`COMPLETENESS` 순수 DEV kernel과 manifest routing은 구현했고, human-rubric artifact·3-pair comparison·실제 Variant 실행은 미구현. 신규 schema/Policy의 승인 version 정렬, HOLDOUT 실행·Release 승인은 별도 게이트.
- [RAG Grounding·Citation Metric 계약 v1 (#160)](./targets/post-mvp-1/rag-grounding-citation-metrics-v1.md): PR #541 책임 리뷰 승인으로 확정된 Approved Target. Claim↔Citation edge, #180 validation·authorization, Gold/source binding, Safety/E2E same-Case grounding signal과 deterministic Citation·unsupported Claim Metric. Schema Set 1.4 Candidate 구현 완료·책임 리뷰 승인 대기.
- [RAG Safety·Rule-first Metric 계약 v1 (#161)](./targets/post-mvp-1/rag-safety-rule-first-metrics-v1.md): PR #541 책임 리뷰 승인으로 확정된 Approved Target. Safety routing·Rule·Scope·invocation·fallback Metric, NOT_INVOKED reversal, #160 same-Case signal과 critical failure exact union.
- [Safety Result·Citation 계약 v2](./targets/post-mvp-1/safety-result-v2.md): Track F에서 v1의 Safety Result·Citation·STALE·Release Gate 목표를 대체하는 후속 Target
- [Safety Result 복합 STALE 우선순위 계약 v1 (`PD-173`)](./targets/post-mvp-1/safety-result-compound-stale-priority-v1.md): 처방 버전·식별 스냅샷·런타임 번들 복합 STALE 동시 발생 시 단일 공개 fallback_code 사영 우선순위(`PRESCRIPTION_STALE` > `IDENTIFICATION_STALE` > `RUNTIME_RELEASE_STALE`)와 내부 `stale_reason` 분리 — Approved Target · Not implemented: 판정 kernel은 병합되었으나 런타임 호출부 없음
- [Protected Retrieval Infrastructure 계약 v1 (`PD-368`, `PD-368-R1`, `PD-368-R2`)](./targets/post-mvp-1/protected-retrieval-infrastructure-v1.md): data-plane 부분 구현, authorization control C1 구현 완료(PR #463), C2 command 및 최소 권한 확장 계약 확정(`PD-368-R2`)

계약 파일의 존재나 문서 승인은 Worker·API·schema 구현 완료 또는 공개 승인을 의미하지 않습니다.

### RAG-00 Approved Target 권위와 책임

RAG Source·Runtime·Evaluation·Medication Candidate·Safety/Citation v2는 외부 Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`의 Local P0 투영본이며 저장소 상태는 `Approved Target · Not implemented`다. 문서 승인만으로 Current Runtime 또는 사용자 공개 완료로 해석하지 않는다. 외부 논리 계약 `medication-candidate-identification-v1`은 별도 파일을 만들지 않고 기존 `medication-identification-v1.md`에 통합했다.

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
- 공식 제품 Resolver는 사용자 확정 `medication_name + nullable strength_text`와 활성 MFDS Catalog만 사용한다. Candidate Index는 #583 lifecycle에 따라 code당 active READY 1개만 소비하며 READY 전 storage-integrity를 재검증한다. 내부 Top-K 중 Single Candidate Gate를 통과한 최대 1개만 표시하고 사용자 확인 전 `MATCHED`로 저장하지 않는다.
- 자동 Guide는 모든 활성 처방약의 현재 Identification이 Runtime Release Bundle과 호환될 때만 Job을 접수한다. Chat은 Identification 완료 전 최소 Safety Intake Job을 접수할 수 있고, `ROUTINE` 분기만 Identification Preflight 후 일반 RAG를 실행한다.
- OTC는 기존 Chat의 질문 유형이며 처방약–OTC Rule·Evidence를 먼저 실행한다. 별도 Track D API·화면·공개 flag는 두지 않는다.
- 비동기 성공 응답은 `{"data": JobStatusResponse}`, 오류는 top-level 공통 오류 envelope를 사용한다.
- 같은 Chat session에는 non-terminal Job을 하나만 허용하고 다른 키의 두 번째 요청은 `409 CHAT_JOB_IN_PROGRESS`다.
- timed occurrence는 사용자가 일정 설정 API에서 시작일·종료 결정·정확한 시각을 확인한 schedule에서만 만든다. 처방에 정확한 시각이 있어도 사용자 확인 없이 자동 생성하지 않으며, Check-in deadline은 `max(Asia/Seoul 기준 예정일 다음 날 00:00, scheduled_at + 4시간)`을 UTC instant로 snapshot한다.
- 비동기 접수의 멱등성 scope는 `(user_id, OpenAPI operation_id, key_hmac)`이고, 동기 상태 변경은 `(user_id, OpenAPI operation_id, parent_resource_id, key_hmac)` scope를 사용한다. 두 레코드 모두 원문 키의 versioned HMAC-SHA-256 결과를 `key_hmac` 컬럼에 저장한다. 둘 다 최소 24시간, 운영 기본값 7일 보존하며 비동기 동일 요청은 기존 Job의 최신 상태를 반환한다.
- 오류: `code`, `message`, `details`, `trace_id`

계약 변경은 관련 요구사항 ID, API 명세, 구현, 테스트와 함께 한 PR에서 갱신합니다. 필드 삭제·이름/타입 변경·필수 필드 추가는 Breaking Change로 취급합니다.

- [Catalog build·approval handoff v2](./targets/post-mvp-1/catalog-build-v2.md): #329 리뷰 반영 구현·검토 대상. 독립 Ingredient, Alias 검색 dedupe, REJECTED 경계, 검증 포트·승인 상태 결속 manifest, Candidate 전체 artifact 검증, 원문 보존·normalized NFC 경계 및 P0 코드 체계 allowlist. 실제 DB·승인 adapter·Runtime 연결은 미완료.
- [Catalog DB 적재·저장 연결안](./proposed/post-mvp-1/catalog-db-integration-v2.md): #166 후속 Proposed. v2 저장 준비·DB transaction·Set/member/hash 보존·읽기 전용 복원과 Candidate 인계 검증을 구현. 별도 Catalog Writer·#436/#444 Source Receipt 소비를 연결. D-03a 조건부 동의·Crosswalk 후속 방향 확인, D-02 및 실제 승인/감사 저장소는 후속.

- [Source reject codes v1 구현 리뷰안](proposed/post-mvp-1/source-reject-codes-v1.md): #165 코드·버전·2-pass·실패 기록. 담당 리뷰 전 proposed, 사용자 지시에 따라 구현·검증 후 리뷰.

### #166 D-03·D-04·D-05 범위와 검토 연결

- [D-03 Catalog Set·Authority Alias Set 관계 및 Crosswalk 범위](../governance/decisions/2026-09-13-catalog-crosswalk-scope.md):
  일반 Catalog 저장·재현 구성에 Alias member를 포함하며 독립 Authority Alias Set을 대체하지 않는다.
  Crosswalk는 현재 P0 소비 경로가 없어 제외. 다섯 항목과 재개 조건을 기록하며 Authority 구현 완료가 아니다.

- [D-05 hash 전환 보류·재개 조건](../governance/decisions/2026-09-13-catalog-d05-transition-scope.md):
  현우님 답변 반영 문서 리뷰 대상. 전체 전환과 Runtime 구성·연결 보류, 기존 v2·관찰 v3 envelope 유지.
  축소 projection은 필요 확인 후 새 계약으로 검토. 신규 hash 구현 완료·공개 승인 아님.

- [Catalog Component occurrence 결정안](../governance/decisions/2026-09-11-catalog-component-occurrences.md):
  Proposed. 선택 원본 키·제품별 순서 UNIQUE·release_profile·무손실 migration 경계.
  계약 정본은 기존 [Catalog DB 연결안](proposed/post-mvp-1/catalog-db-integration-v2.md)을 갱신한다.
  2026-09-13 후속 구현: MFDS 관찰 입력의 총량 그룹·원본 필드·제외 사유·건수 검사.
  검증된 상세 artifact 이후 Loader → DB 저장·복원 → Candidate 인계는 구현·합성 검증 및
  [정현우 담당 범위 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/477#pullrequestreview-5190458759)를 완료했다. 실제 API 수집·상세 Snapshot 생산은 후속이다.
  승인 대상 HEAD는 `c58f0968`이며 병합·운영 활성화와 구분한다.

- [D-04 관찰 출처·인계 v3 결정안](../governance/decisions/2026-09-13-component-observation-handoff.md):
  검증된 상세 artifact Loader, 독립 Snapshot FK, 관찰 버전·총량 그룹의 DB/Candidate 인계 제안.
  기존 v2를 보존하고 관찰 자료는 medication-catalog-v3로 구분. 계약 정본은 위 Catalog DB 연결안.

- [D-04 MFDS 상세 수집·Snapshot 생산](./proposed/post-mvp-1/mfds-detail-acquisition-166.md): 전체 범위 전용 수집·원문 보존·빈 행 차단·상세 Receipt 검증과 기존 Source lifecycle 연결. 구현 PR 리뷰 대상이며 실제 API 수집·승인은 별도.

### #166 실제 승인 저장소 후속 제안

- [승인·철회·감사 저장소 구체안](proposed/post-mvp-1/catalog-approval-storage-166.md):
  Proposed. 기존 관리 감사와 승인 receipt 저장을 구분하고, 승인 대상·포트·transaction·최소 권한·이행 및 검증 범위를 제안한다.
  실제 연결·DB 변경·신규 승인 획득은 미완료이며 기존 D-03/D-05 합의의 승인 범위에 포함하지 않는다.

## Track C C1 저장 기반 (#192)

- [저장 계약 v1 — Proposed/내부 저장 기반 구현 완료](proposed/track-c-storage-v1.md)
- [PD-192](../governance/decisions/2026-09-13-track-c-storage-192.md)
- 실제 Check-in 부모에 Safety·Barrier·Plan·Follow-up 이력을 연결하는 저장 기반이다.
  공개 mutation, #195 무효화와 Track C 공개는 미완료다.
- [HandlerConfig 구체안 — Proposed/제품 승인·내부 구현](proposed/track-c-handler-config-192.md):
  기존 Plan JSONB 기반 설정·버전·지원별 허용 필드 및 #194 실행 인계. 제품 승인 Rule·한국어 Copy와
  명시적 allowlist·엄격 로더를 구현했으며 담당 기술·화면 리뷰와 공개 실행은 미완료다.
- [PD-192-2](../governance/decisions/2026-09-15-track-c-handler-config-rules-192.md):
  6개 최소 안내형 Support의 내부 Rule·Copy 제품 승인과 공개 전 경계.

## Track C C2 Safety·Barrier API (#193)

- [API v1 — Proposed](proposed/track-c-safety-barrier-api-193.md): HTTP·DTO·멱등성·정정·동시성 구현 리뷰 대상.
- [PD-193](../governance/decisions/2026-09-15-track-c-safety-barrier-api-193.md): 구체화 delta와 NHS 참고 자료 경계.
- [Safety 정책·한국어 문구 검토안](proposed/track-c-safety-policy-copy-193.md): NHS 근거별 선택표, 복수 선택 규칙, 고정 안내와 합성 검증 명세. 문서 초안이며 런타임 미적용.

## Track C 지원 1개 제안·Plan 생성 (#194 부분)

- [API v1 — Proposed, 리뷰 revision 2](proposed/track-c-support-plan-api-194.md): 잠금 없는 단일 제안 GET, 설정 로딩 후 잠그는 확정 POST, 현재성·소유권·멱등성.
- [PD-194](../governance/decisions/2026-09-15-track-c-support-plan-api-194.md): PM의 0/1개 결정과 이번 구현 범위 축소.
- Plan 조회·완료·취소·follow-up·revision 추가는 이번 범위 밖이며 #194 전체 완료가 아니다.
- 실제 증상별 임상 판정표·환자용 문구 및 #195 연결은 미완료다.


### #458 Worker 동의 조회·재검사 로컬 구현

- [후속 계약](proposed/ocr-llm-transfer-458.md)
- [#465 대조·구현 범위 및 runtime 연결 조건](../designs/ocr-consent-gate-458-implementation.md)
- [동의 조회·재검사 검증](../testing/ocr-consent-gate-458.md)
- 내부 조회와 호출 직전 검사 부품. 동의 API·차단 저장·전송 최소화·공개 활성화 완료 아님.

## Track C 완료 계획 Follow-up (#194 후속)

- [Follow-up API v1 — Current, PR #631 반영](current/track-c-followup-api-194.md): 완료 계획의 평가 GET/POST, 현재값·revision 정정·audit·멱등성·동시성. 기존 생성/Plan 응답은 유지한다. 최종 책임 리뷰 승인·병합은 대기 중이며 외부 공개는 별도다.
- [PD-194-2](../governance/decisions/2026-09-16-track-c-followup-194.md): 완료 계획만 평가, 이후 Check-in 정정에도 과거 평가 허용. 권가빈 구현·김지혜 단일 책임 리뷰.
- #139 Frontend·Constraint/RAG Handler·외부 공개 및 #194 전체 완료는 별도 범위다.
