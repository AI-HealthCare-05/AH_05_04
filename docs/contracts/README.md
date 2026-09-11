# 공통 데이터 계약

Frontend, Backend, OCR과 RAG·LLM이 공유하는 의미와 상태를 관리합니다. **현재 실행 계약**은 실제 FastAPI OpenAPI·Pydantic DTO·migration·구현과 테스트가 함께 뒷받침하는 문서입니다. `targets/post-mvp-1/`의 문서는 승인된 Post-MVP-1 목표 계약이며 문서별 구현 상태를 별도로 표시하고 Approved Contract Freeze v4와 RAG-00 Approved Target을 함께 관리합니다.

상태와 승인 원본의 우선순위는 [Post-MVP-1 문서 권위](../governance/post-mvp-1-document-authority.md)를 따릅니다.

- [Source Attempt Receipt 실패 보완 (#436)](./proposed/source-attempt-receipt-436.md): COLLECTION_FAILED 및 EMPTY_RESULT 계열 구분 구현 리뷰안.

## 디렉터리 구조와 배치 원칙

- [PD-398-M1 Source·Catalog 관리](./proposed/source-catalog-management-398.md): 분리된 관리 API, 서버 권한, 미사용 자료 수정·삭제와 감사 transaction, PD-398-R2 Snapshot 잠금 권한 분리.
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

- [복약 가이드 Backend–AI 계약](./current/medication-guide-ai-backend.md): `guide-prompt-v3` intent·승인 문구 선택형 동기 one-cycle 입력·출력·오류 경계
- [복약 챗봇 Backend–AI Core 계약](./current/medication-chat-ai-backend.md): 현재 동기 `201` 생성과 세션 직렬화 경계
- [OCR 약품명 정규화 계약](./current/ocr-medication-normalization.md): OCR 원문, 정규화 참고값 및 사용자 확정값의 역할
- [OCR Provider 약품명 필드 Alias 예방 계약](./current/ocr-provider-field-aliases.md): 현재 외부 alias가 없는 상태에서 `medication_name`·`MEDICATION_NAME` 정본과 향후 Source별 Provider Adapter 변환 경계를 고정
- [OCR 약품 행 구조화 계약](./current/ocr-medication-structuring.md): #144 빈 검수 필드 개정은 작업 브랜치 검증 완료·리뷰/병합 대기. 현재 약품 행 판정·부분 인식·사용자 확인 경계
- [처방 확정 Backend 계약](./current/prescription-confirmation.md): OCR 검수 필드로 처방을 확정할 때의 필수값, DB 경계값, Post-MVP `job_id` 검증 경계
- [회원가입·사용자 정보 계약](./current/user-account.md): 회원가입 허용 필드, 내 정보 수정 범위, 개인정보 nullable 상태, 현재 구현된 `token_version` 기반 인증 세션 무효화
- [OCR 작업 상태 조회 계약](./current/ocr-job-status.md): OCR 작업 실패 코드와 `error_message` 노출 기준, 최신 작업 판별 기준
- [공통 Job 상태 조회 계약 v1](./current/job-status-v1.md): `GET /api/v1/jobs/{job_id}` 응답 필드, 6개 Job 상태 의미, 소유권 이중 확인, 오류 계약
- [Backend 공통 오류 응답 계약](./current/backend-error-response.md): `ApiError` 사용법, 공통·도메인 오류 코드
- [Local Live Provider 호출 증적 계약](./current/live-provider-call-evidence.md): `local-live-full` 요청 상관관계, Provider JSONL과 수동 증빙 판정
- [Backend 공통 구현 규칙](./current/backend-common-patterns.md): 소유권 확인, 실패 상태 저장
- [PROFILE SELF 소유권 전환 계약 v1](./current/profile-self-ownership-v1.md): 본인 단일 SELF profile과 `profile_id` 기반 사용자 리소스 소유권 기준
- 공통 오류: `code`, `message`, `details`, `trace_id`

## Proposed 계약

- [목적별 동의 Gate 계약 제안 (PD-207)](./proposed/consent-gate-207.md): OCR/GUIDE/CHAT/NOTIFICATION 목적별 GRANTED/WITHDRAWN 동의 상태, row 없음=미동의, Backend·Worker 공통 fixture 판정, WorkerMessage/Stream 비전송, CONSENT_REQUIRED 및 OCR CONSENT_WITHDRAWN 차단 의미. Proposed · 미구현 · Production 공개 승인 아님. 확인 필요: 권가빈·김지혜·정현우·남한솔.

- [Track B UNCONFIRMED backlog v1](./proposed/unconfirmed-backlog-v1.md): PD-418 URL·cursor·DTO·오류 제안. 실제 v1 router 등록 보류, 테스트 앱의 #413 PUT→GET HTTP 통합 검증. Cursor 404의 첫 페이지 재조회 명시, 계약 및 등록 HEAD의 Backend/Frontend 승인 필요.

- [Track B Notification 계약 v1 (#203)](./proposed/track-b-notifications-v1.md): 알림 저장·게시·읽음·재알림 상세 제안. PD-203 도메인 조율 대기·미구현이며 #202와의 통합 접점 및 검증 계획 포함.

- [Source Artifact·REJECTS 보존·삭제 정책 초안 (#335)](./proposed/post-mvp-1/source-artifact-retention-cleanup.md): PM 30일 유예·참조 보존·수동 배치 승인 반영, 통합 검토 대상, 후속 구현 [#347](https://github.com/AI-HealthCare-05/AH_05_04/issues/347)·김지혜 담당. Local 합성 #347의 승인 순서·revision·경합 잠금·DB 감사 근거·참조 범위 보완 연결 포함. 운영 삭제·활성화 승인 아님.

- [Staging Release Validation Ledger 계약](./proposed/operations/release-validation-ledger.md): staging control DB, 상태 전이, crash recovery와 migration 상호 배제
- [개발환경·비밀정보 주입 경로 점검 운영 계약](./proposed/operations/development-env-secret-injection-check.md): Redis, PostgreSQL, Provider secret 주입 경로와 운영 배포 전 차단 조건
- [Track A migration·rollback 계획 제안 v1](./proposed/track-a-migration-rollback-v1.md): 문서 상태 Proposed · 구현 상태 Partially implemented — 공통 Job 기반과 OCR–AI Job mapping을 구현했으며 Guide·Chat 연결, Prescription Version, 전체 비동기 전환·backfill·read cutover는 미구현
- [계정 생명주기 후속 계약 v1 (`PD-206`)](./proposed/account-lifecycle-v1.md): 회원탈퇴의 transaction 경계와 후속 구현 기준. 로그아웃·`token_version` 재검증·refresh token rotation·비밀번호 재설정은 현재 구현 계약([`user-account.md`](./current/user-account.md))에 반영됨
- [Guide·Chat Session·Message 상태 구현 골격 v1](./proposed/guide-chat-session-message-status-ui-v1.md): Session/Message/Job 결과 상태축, SAFETY-STALE 경계, PROFILE 기반 소유권의 Frontend 구현 골격

Proposed 계약은 문서별 구현 상태를 별도로 표시합니다. 부분 구현은 전체 계약 완료나 Current 승격을 의미하지 않으며, 관련 schema·service·CLI·테스트와 남은 전환 단계가 완료되고 상태가 갱신되기 전에는 실행 가능한 전체 계약으로 간주하지 않습니다.

## 승인된 Post-MVP-1 목표 계약

- [Post-MVP-1 목표 계약 인덱스](./targets/post-mvp-1/README.md)
- [비동기 Job 계약 v1](./targets/post-mvp-1/async-job-v1.md): Job 유형, 6개 상태, Chat 동시성 및 Polling — Job 상태 조회 GET과 OCR 접수 POST 구현 완료(#148) · rediscovery GET은 서비스 로직 구현·라우트 등록 보류 · Guide/Chat 접수 POST와 Reconciler 미구현
- [멱등성 계약 v1](./targets/post-mvp-1/idempotency-v1.md): 요청 지문, 중복·충돌 처리와 보존 기간
- [Transactional Outbox와 Redis Stream 계약 v1](./targets/post-mvp-1/outbox-stream-v1.md): at-least-once 전달, ACK, fencing과 메시지 경계
- [처방 버전 계약 v1](./targets/post-mvp-1/prescription-version-v1.md): 불변 snapshot, 활성화, stale 및 기존 데이터 backfill
- [Check-in과 Barrier 계약 v1](./targets/post-mvp-1/checkin-v1.md): 3개 Check-in 결과와 Barrier 명시적 거절·미제출 구분; #202 Check-in PUT 부분 구현·Draft 리뷰 대기, [HTTP 구체화 제안](../governance/decisions/2026-09-10-checkin-api-202.md)
- [OCR 비-RAG LLM 구조화 계약 v1](./targets/post-mvp-1/ocr-llm-structuring-v1.md): 최소전송, 구조화 초안 provenance, 사용자 확정과 실패 복구
- [MFDS 공식 의약품 식별·Candidate 계약 v1](./targets/post-mvp-1/medication-identification-v1.md): 공식 Source/Catalog·후보 검색·사용자 확인·Preflight 공유 경계
- [Safety Result 계약 v1](./targets/post-mvp-1/safety-result-v1.md): Approved v4 이력과 Track C 공통 Safety 기준; Track F 후속 의미는 v2가 대체
- [RAG Source 수집·활성화 계약 v1](./targets/post-mvp-1/rag-source-ingestion-v1.md): Source 승인, 수집·검증·활성화와 Index 결속 · MFDS 제품 `mfds-product-approval@1` canonicalization과 `ProductIngestionResult` 경계, 실패 재시도 충돌·Verification 불변성·REJECTS 1:1·DB-owned 상태 전이 검증 구현 중(#165), 비FAILED 계보 유지·#335/#347 보존·정리 완료·#362 정책/외부 version/시도 provenance 영속화 구현 및 #165 잔여 allowlist 계약 추적 #436은 invalid Version의 저장 진입 감사와 명시적인 Attempt decision 복원을 포함한다.
- [RAG Runtime 계약 v1](./targets/post-mvp-1/rag-runtime-v1.md): Guide·Chat·OTC의 Rule-first·Retrieval·Citation·Safety 공통 흐름, Runtime Bundle의 Manifest Hash·저장 정합 계약(`PD-175-20260910` Approved)
- [Guideline Card typed port 계약 v1](./targets/post-mvp-1/guideline-card-v1.md): RAG-14 Evidence Gate·PD-362 Source eligibility와 RAG-16 사이의 Request·Outcome·승인 verifier·fallback 계약 — RAG-15 persistence-free kernel 구현 검토 중(#179, PR #414), Current 아님
- [RAG Evaluation·Release Gate 계약 v1](./targets/post-mvp-1/rag-evaluation-v1.md): RAG 전후 비교, 필수 Metric, Schema Set 1.3 `Candidate · Review Required` provenance 계약과 Release 차단 기준
- [Safety Result·Citation 계약 v2](./targets/post-mvp-1/safety-result-v2.md): Track F에서 v1의 Safety Result·Citation·STALE·Release Gate 목표를 대체하는 후속 Target
- [Safety Result 복합 STALE 우선순위 계약 v1 (`PD-173`)](./targets/post-mvp-1/safety-result-compound-stale-priority-v1.md): 처방 버전·식별 스냅샷·런타임 번들 복합 STALE 동시 발생 시 단일 공개 fallback_code 사영 우선순위(`PRESCRIPTION_STALE` > `IDENTIFICATION_STALE` > `RUNTIME_RELEASE_STALE`)와 내부 `stale_reason` 분리 — Approved Target · Not implemented: 판정 kernel은 병합되었으나 런타임 호출부 없음
- [Protected Retrieval Infrastructure 계약 v1 (`PD-368`)](./targets/post-mvp-1/protected-retrieval-infrastructure-v1.md): data-plane 제한 로그인·승인 artifact hash 결속·durable INTENT/UNKNOWN 부분 구현, control-plane grant/revoke/FREEZE 서비스 미구현

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
- 공식 제품 Resolver는 사용자 확정 `medication_name + nullable strength_text`와 활성 MFDS Catalog만 사용한다. 내부 Top-K 중 Single Candidate Gate를 통과한 최대 1개만 표시하고 사용자 확인 전 `MATCHED`로 저장하지 않는다.
- 자동 Guide는 모든 활성 처방약의 현재 Identification이 Runtime Release Bundle과 호환될 때만 Job을 접수한다. Chat은 Identification 완료 전 최소 Safety Intake Job을 접수할 수 있고, `ROUTINE` 분기만 Identification Preflight 후 일반 RAG를 실행한다.
- OTC는 기존 Chat의 질문 유형이며 처방약–OTC Rule·Evidence를 먼저 실행한다. 별도 Track D API·화면·공개 flag는 두지 않는다.
- 비동기 성공 응답은 `{"data": JobStatusResponse}`, 오류는 top-level 공통 오류 envelope를 사용한다.
- 같은 Chat session에는 non-terminal Job을 하나만 허용하고 다른 키의 두 번째 요청은 `409 CHAT_JOB_IN_PROGRESS`다.
- timed occurrence는 사용자가 일정 설정 API에서 시작일·종료 결정·정확한 시각을 확인한 schedule에서만 만든다. 처방에 정확한 시각이 있어도 사용자 확인 없이 자동 생성하지 않으며, Check-in deadline은 `max(Asia/Seoul 기준 예정일 다음 날 00:00, scheduled_at + 4시간)`을 UTC instant로 snapshot한다.
- 비동기 접수의 멱등성 scope는 `(user_id, OpenAPI operation_id, key_hmac)`이고, 동기 상태 변경은 `(user_id, OpenAPI operation_id, parent_resource_id, key_hmac)` scope를 사용한다. 두 레코드 모두 원문 키의 versioned HMAC-SHA-256 결과를 `key_hmac` 컬럼에 저장한다. 둘 다 최소 24시간, 운영 기본값 7일 보존하며 비동기 동일 요청은 기존 Job의 최신 상태를 반환한다.
- 오류: `code`, `message`, `details`, `trace_id`

계약 변경은 관련 요구사항 ID, API 명세, 구현, 테스트와 함께 한 PR에서 갱신합니다. 필드 삭제·이름/타입 변경·필수 필드 추가는 Breaking Change로 취급합니다.

- [Catalog build·approval handoff v2](./targets/post-mvp-1/catalog-build-v2.md): #329 리뷰 반영 구현·검토 대상. 독립 Ingredient, Alias 검색 dedupe, REJECTED 경계, 검증 포트·승인 상태 결속 manifest, Candidate 전체 artifact 검증, 원문 보존·normalized NFC 경계 및 P0 코드 체계 allowlist. 실제 DB·승인 adapter·Runtime 연결은 미완료.

- [Source reject codes v1 구현 리뷰안](proposed/post-mvp-1/source-reject-codes-v1.md): #165 코드·버전·2-pass·실패 기록. 담당 리뷰 전 proposed, 사용자 지시에 따라 구현·검증 후 리뷰.
