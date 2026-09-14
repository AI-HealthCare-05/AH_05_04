# #458 Worker 동의 차단 저장 결정안

- 상태: Proposed / 로컬 구현·검증 중. 담당 리뷰어 승인 및 Backend·Frontend 연동 전.
- 구현 담당: 김지혜. 담당 리뷰어: 송은영 — Worker/OCR·공통 Job 저장·Backend/Security·Frontend 사유 소비 영향.
- 정책 근거: [가빈 11:07](https://discord.com/channels/@me/1536183621747220564/1548877830459490437).
- 기술 근거: [은영 이전 답변](https://discord.com/channels/@me/1546828697879969832/1548872499838718034),
  [#465 병합 후 답변](https://discord.com/channels/@me/1546828697879969832/1548877992795701439).
- 영향 계약: [OCR 동의·전송 계약안](../../contracts/proposed/ocr-llm-transfer-458.md).

## 문제와 선택

#465는 동의 저장 기반이다. Worker의 기존 Dispatcher는 동의 철회 예외를 일반 INTERNAL_ERROR로
바꾸므로, Gate만 연결하면 재시도되거나 철회가 일반 실패로 표시된다. 전용 내부 예외를
안전한 사유로 전달하고 현재 lease 소유자만 다음 상태를 하나의 transaction으로 저장한다.

| Gate 사유 | OCR error_code | Job / Attempt / OCR |
| --- | --- | --- |
| WITHDRAWN | CONSENT_WITHDRAWN | STALE / BLOCKED / FAILED |
| MISSING_CONSENT, PURPOSE_MISMATCH, INVALID_CONSENT | CONSENT_REQUIRED | STALE / BLOCKED / FAILED |
| POLICY_VERSION_MISMATCH | CONSENT_POLICY_MISMATCH | STALE / BLOCKED / FAILED |
| LOOKUP_FAILED | CONSENT_LOOKUP_FAILED | STALE / BLOCKED / FAILED |
| ACCOUNT_NOT_ACTIVE, OWNER_MISMATCH | CONSENT_SUBJECT_INVALID | STALE / BLOCKED / FAILED |

철회의 상태 방향은 은영 답변에 근거한다. 그 외 사유 매핑과 조회 실패의 **자동 재시도 없음**은
이번 검토용 구체안이다. 조회 복구·재동의가 과거 작업을 자동 재개하지 않으며 새 접수는 Backend
Gate를 통과해야 한다. API의 HTTP status, 접수 오류 상세, 동의 조회/등록/철회 route는 이 결정으로
확정하지 않는다. `STALE`만 보고 철회를 추정하지 말고 OCR 사유를 함께 소비한다.

공통 WorkerFailureCode를 늘리지 않는다. Job.failure_code와 Attempt.error_code는 null이며
명시적 사유는 OCR.error_code에 기록한다. Attempt.retryable/timed_out은 false다.
성공 필드를 저장하지 않고 Job·Attempt·OCR 변경이 모두 성공한 commit 이후에만 ACK한다.
일부 row 없음·lease 만료·다른 event/attempt/token이면 성공 처리하지 않는다. 동일 event 재전달은
기존 STALE 소비 기록으로 ACK하며 새 Attempt/Provider 호출을 만들지 않는다.

## 동의 검사와 범위

- #207/#465 user_consent 및 공통 fixture를 재사용한다. Worker에서 Backend ORM/Service를 import하지 않는다.
- CLOVA 직전, LLM 직전, 결과를 성공 저장 경로에 넘기기 전에 각각 새 session으로 조회한다.
  마지막 검사는 LLM을 사용하지 않는 경우의 CLOVA 실행 중 철회도 차단하기 위해 필요하다.
- OCR_CONSENT_POLICY_VERSION은 빈 값이 기본이며 빈 값은 fail-closed다. fixture 버전을 운영 정책으로
  자동 지정하지 않는다. 변경 고지·정책 버전·재동의의 최종 확정은 별도다.
- 검사 후 전송/commit 사이 경합이나 이미 전송된 요청의 회수까지 보장하지 않는다.
  저장 후 철회된 과거 결과의 Backend 접근 차단은 별도 Backend 연계 작업이다.
- RLS·Trigger·업무 DB 함수·migration을 추가하지 않고 Python 검증과 명시적 transaction을 사용한다.

## 데모 및 남은 경계

철회 후 기존 OCR 결과 재노출·검수·수정은 데모에서 제외한다. 보관 허용이나 삭제 지시로
해석하지 않는다. 보관·삭제 정책은 실제 사용자 적용 전 확정한다. OCR Job 없는 완전 수동 입력은
기존 경로 확인 후 새 구현이 필요하면 후속으로 분리하며 #458/데모 완료 필수조건이 아니다.

이 결정은 전송 최소화 완료나 활성화 승인이 아니다. 기존 전체-token LLM 입력의 대체,
처방일 로컬 추출·필수 검수 보존, 생략 metadata/DTO, Backend API·접수 Gate, Frontend 표시
연결은 아직 남아 있다. 기존 LLM 비활성 기본값과 Privacy Production/외부 승인 조건을 유지한다.
