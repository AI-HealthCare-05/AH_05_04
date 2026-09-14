# #458 OCR 동의 차단·전송 경계 결정안

- 상태: Proposed / Worker·Backend·Frontend 로컬 연결 완료. 담당 리뷰어 승인·최종 안내 문구·실사용 활성화 전.
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
Gate를 통과해야 한다. API의 HTTP status·접수 오류·동의 route는 아래와 같이 로컬 구현했으며
담당 리뷰어 검토 전까지 Proposed이다. `STALE`만 보고 철회를 추정하지 말고 OCR 사유를 함께 소비한다.

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
  저장 후 철회된 과거 완료 결과는 Backend 조회·검수·확정 경로에서 현재 동의를 재검사해 차단한다.
- RLS·Trigger·업무 DB 함수는 추가하지 않고 Python 검증과 명시적 transaction을 사용한다.

## 데모 및 남은 경계

철회 후 기존 OCR 결과 재노출·검수·수정은 데모에서 제외한다. 보관 허용이나 삭제 지시로
해석하지 않는다. 보관·삭제 정책은 실제 사용자 적용 전 확정한다. OCR Job 없는 완전 수동 입력은
기존 경로 확인 후 새 구현이 필요하면 후속으로 분리하며 #458/데모 완료 필수조건이 아니다.

## Backend·Frontend 연계와 안전한 생략

- Backend는 현재 `OCR_CONSENT_POLICY_VERSION`과 `user_consent`의 OCR row를 비교한다. 빈 버전은
  `503 CONSENT_POLICY_UNAVAILABLE`, 조회 실패는 `503 CONSENT_LOOKUP_FAILED`로 닫는다.
  접수 전 미동의·철회·버전 불일치는 `403 CONSENT_REQUIRED`다.
- `GET/POST/DELETE /api/v1/users/me/consents/OCR`은 현재 상태·유효성·불일치 사유·수락 버전을
  반환한다. POST는 현재 버전만 수락하고, DELETE는 기존 버전을 보존해 철회한다. 정책 버전 설정이
  없어 새 접수·동의 등록이 차단돼도 기존 동의 철회는 허용한다.
- OCR 접수는 외부 처리 전에 확인하고 문서 row lock 이후 재검사한다. 기존 완료 OCR 결과
  조회·검수 수정·수동 약물 추가·처방 확정도 현재 유효 동의가 없으면 차단한다.
  Frontend는 검수 화면에서 prefetched 결과를 보여주기 전에도 현재 상태를 확인한다.
- 공통 Job의 `STALE`은 단독으로 철회를 의미하지 않는다. Frontend는 OCR 도메인 `error_code`를
  별도로 조회해 `CONSENT_WITHDRAWN`을 구분한다.
- 승인된 약품명 selector가 아직 없어 LLM 구조화 기능이 켜져도 전체 OCR token이나 추정한
  부분 문자열을 외부 LLM에 보내지 않는다. 로컬 규칙 결과를 검수에 제공하며
  `ocr_job.llm_processing=SKIPPED_MINIMIZATION`으로 저장한다. 기능이 꺼진 경우는
  `NOT_REQUESTED`, 기존/미확인 결과는 null이다. `APPLIED`는 검증된 selector의 미래 값이다.
  처방일은 LLM에 보내지 않고 로컬 OCR 규칙과 기존 필수 검수로 다룬다.
- 새 nullable 컬럼과 일반 CHECK 제약을 migration에 추가했다. 업무 판단은 Service/Repository와
  명시적 transaction에 남겨 두며 RLS·DB Trigger는 사용하지 않는다.

최종 안내 문구·정책 버전과 약품 전송 selector 검토가 끝나기 전에는 실제 사용자 대상 LLM을
활성화하지 않는다. 이 결정안은 담당 리뷰어 승인 전 Proposed다.
