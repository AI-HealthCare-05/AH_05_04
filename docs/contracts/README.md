# 공통 데이터 계약

Frontend, Backend, OCR과 RAG·LLM이 공유하는 의미와 상태를 관리합니다. **현재 실행 계약**은 실제 FastAPI OpenAPI·Pydantic DTO·migration·구현과 테스트가 함께 뒷받침하는 문서입니다. `*-v1.md` 신규 문서는 **승인된 Post-MVP-1 목표 계약**이며 해당 구현 PR이 병합되기 전에는 현재 API·DB 동작으로 간주하지 않습니다.

상태와 승인 원본의 우선순위는 [Post-MVP-1 문서 권위](../governance/post-mvp-1-document-authority.md)를 따릅니다.

## 디렉터리 구조와 배치 원칙

계약 문서는 구현 상태가 아니라 변경 책임과 탐색 경로가 안정적인 도메인별 하위 디렉터리에 둡니다.

- `accounts/`: 사용자 계정과 개인정보 입력 경계
- `jobs/`: 비동기 Job, 멱등성, Outbox·Stream 실행 기반
- `medication/`: 복약 가이드·챗봇·Check-in 등 복약 경험
- `ocr/`: OCR 처리, 결과 구조화와 상태 조회
- `operations/`: 배포·검증 등 저장소 운영 계약
- `prescriptions/`: 처방 확정과 처방 버전
- `safety/`: AI 결과 검증·공개와 의료 안전 경계

`Current`, `Approved target / Not implemented`, `Proposed`는 경로가 아니라 각 문서의 상태 표기와 이 인덱스에서 관리합니다. 상태가 바뀌어도 파일을 다른 폴더로 이동하거나 복제하지 않습니다. 신규 계약은 가장 가까운 도메인에 하나의 정규 경로로 추가하고, 여러 도메인에 걸치면 주된 변경 책임이 있는 도메인에 둔 뒤 이 인덱스에서 연결합니다.

## 현재 구현 계약

- [복약 가이드 Backend–AI 계약](./medication/medication-guide-ai-backend.md): 현재 동기 one-cycle 입력·출력·오류 경계
- [복약 챗봇 Backend–AI Core 계약](./medication/medication-chat-ai-backend.md): 현재 동기 `201` 생성과 세션 직렬화 경계
- [OCR 약품명 정규화 계약](./ocr/ocr-medication-normalization.md): OCR 원문, 정규화 참고값 및 사용자 확정값의 역할
- [OCR 약품 행 구조화 계약](./ocr/ocr-medication-structuring.md): 현재 약품 행 판정·부분 인식·사용자 확인 경계
- [처방 확정 Backend 계약](./prescriptions/prescription-confirmation.md): OCR 검수 필드로 처방을 확정할 때의 필수값, DB 경계값, Post-MVP `job_id` 검증 경계
- [회원가입·사용자 정보 계약](./accounts/user-account.md): 회원가입 허용 필드, 내 정보 수정 범위와 개인정보 nullable 상태
- [OCR 작업 상태 조회 계약](./ocr/ocr-job-status.md): OCR 작업 실패 코드와 `error_message` 노출 기준, 최신 작업 판별 기준
- 공통 오류: `code`, `message`, `details`, `trace_id`

## Proposed 운영 계약 — 미구현

- [Staging Release Validation Ledger 계약](./operations/release-validation-ledger.md): staging control DB, 상태 전이, crash recovery와 migration 상호 배제

Proposed 운영 계약은 관련 schema·service·CLI·테스트가 함께 병합되고 상태가 갱신되기 전에는 실행 가능한 계약으로 간주하지 않습니다.

## 승인된 Post-MVP-1 목표 계약 — 미구현

- [비동기 Job 계약 v1](./jobs/async-job-v1.md): Job 유형, 6개 상태, Chat 동시성 및 Polling
- [멱등성 계약 v1](./jobs/idempotency-v1.md): 요청 지문, 중복·충돌 처리와 보존 기간
- [Transactional Outbox와 Redis Stream 계약 v1](./jobs/outbox-stream-v1.md): at-least-once 전달, ACK, fencing과 메시지 경계
- [처방 버전 계약 v1](./prescriptions/prescription-version-v1.md): 불변 snapshot, 활성화, stale 및 기존 데이터 backfill
- [Check-in과 Barrier 계약 v1](./medication/checkin-v1.md): 3개 Check-in 결과와 Barrier 명시적 거절·미제출 구분
- [Safety Result 계약 v1](./safety/safety-result-v1.md): 생성·검증·공개 상태 조합과 fail-closed 규칙

계약 파일의 존재나 문서 승인은 Worker·API·schema 구현 완료 또는 공개 승인을 의미하지 않습니다.

### Current 승격 조건

목표 계약은 도메인별 정규 경로를 유지한 채 상태를 갱신한다. 관련 코드·migration·OpenAPI/DTO, 계약·통합 테스트와 실행 증빙이 같은 구현 PR에 포함되고 관련 영역의 지정 리뷰어 승인을 받은 뒤에만 Current로 표시한다. 외부 승인이나 공개 flag가 필요한 기능은 이 승격과 별도로 [외부 승인 게이트](../release-gates/post-mvp-1-external-approvals.md)를 충족해야 한다.

### Contract Freeze v1에서 확정한 목표

- 비동기 Job은 `PENDING`, `PROCESSING`, `RETRY_WAIT`, `COMPLETED`, `FAILED`, `STALE`의 6개 상태를 사용한다.
- `REVIEW_REQUIRED`는 OCR 결과 검수 상태이며 Job 상태가 아니다.
- Check-in 저장 결과는 `TAKEN`, `NOT_TAKEN`, `UNCONFIRMED`의 3개다.
- Barrier의 명시적 건너뛰기·거절은 `response_status=DECLINED`, `barrier_code=null`로 표현하고, 단계에 진입하지 않거나 제출하지 않은 미제출은 응답 row를 생성하지 않는다.
- 처방 하위 결과는 불변 `prescription_version_id`에 귀속하고 최신 버전이 아니면 `STALE` 처리한다.
- AI 결과는 생성·검증·공개 결정을 분리하고 근거 부족과 검증 실패를 fail-closed 처리한다.
- 비동기 성공 응답은 `{"data": JobStatusResponse}`, 오류는 top-level 공통 오류 envelope를 사용한다.
- 같은 Chat session에는 non-terminal Job을 하나만 허용하고 다른 키의 두 번째 요청은 `409 CHAT_JOB_IN_PROGRESS`다.
- timed occurrence는 사용자가 일정 설정 API에서 시작일·종료 결정·정확한 시각을 확인한 schedule에서만 만든다. 처방에 정확한 시각이 있어도 사용자 확인 없이 자동 생성하지 않으며, Check-in deadline은 `max(Asia/Seoul 기준 예정일 다음 날 00:00, scheduled_at + 4시간)`을 UTC instant로 snapshot한다.
- 비동기 접수의 멱등성 scope는 `(user_id, OpenAPI operation_id, key_digest)`이고, 동기 상태 변경은 별도 `(user_id, OpenAPI operation_id, parent_resource_id, key_hmac)` scope를 사용한다. 둘 다 최소 24시간, 운영 기본값 7일 보존하며 비동기 동일 요청은 기존 Job의 최신 상태를 반환한다.
- 오류: `code`, `message`, `details`, `trace_id`

계약 변경은 관련 요구사항 ID, API 명세, 구현, 테스트와 함께 한 PR에서 갱신합니다. 필드 삭제·이름/타입 변경·필수 필드 추가는 Breaking Change로 취급합니다.
