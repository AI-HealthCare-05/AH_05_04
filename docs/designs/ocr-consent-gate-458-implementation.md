# #458 Worker 동의 조회·호출 직전 검사 구현 단위

- 구현 담당: 김지혜. 담당 리뷰어: 송은영(Backend/Security·Worker 동의 조회 경계).
- Privacy/Product 정책 증빙: 권가빈 답변 별도. Frontend 소비 계약: 남한솔 답변 별도.
- 기준: develop `a440d2ae` (#465 병합), 로컬 동기화 merge `3a77d956`.
- 상태: Worker·Backend API·Frontend 로컬 연결 및 안전한 LLM 생략 구현. 최종 정책 안내·실제 LLM 전송·공개 활성화는 미완료.

## 2026-09-14 은영 답변 반영

[은영 이전 답변](https://discord.com/channels/@me/1546828697879969832/1548872499838718034) 및
[#465 병합 후 답변](https://discord.com/channels/@me/1546828697879969832/1548877992795701439) 기준이다.
데모 범위는 [가빈 11:07 답변](https://discord.com/channels/@me/1536183621747220564/1548877830459490437)에 따른다.

- #207/#465는 migration/model/repository/공통 fixture 인계 기준이다. 동의 API와 실제 Gate는 #458 후속 변경에서 연결했다.
- Backend 접수 전 검사, Worker CLOVA 직전 및 LLM 직전 재검사로 분담한다.
- row 없음/철회/policy mismatch/조회 실패는 외부 호출을 차단한다.
- 접수 전 미동의·철회·버전 불일치는 CONSENT_REQUIRED 계열이다.
- 접수 후 철회는 BLOCKED + STALE, OCR FAILED + CONSENT_WITHDRAWN 방향이며 실제 저장 경로는 #458에서 확정한다.
- 유효 동의·LLM만 생략된 경우 기존 OCR 검수·수정 흐름 재사용 방향이다.
- 완전 수동 입력 저장 경로는 은영이 추가 확인한다. 새 구현은 후속 분리 가능하며 데모 필수조건이 아니다.
- 철회 후 결과 재노출·검수·수정은 데모 제외. 저장·보관·삭제 정책은 실제 사용자 적용 전 확정한다.
- 사용자 확인: 은영 추가 질문과 가빈 정책 질문은 발송 완료. 한솔 추가 질문 없음.

## 구현

1. `SqlAlchemyOcrConsentRepository`는 Backend ORM import 없이 SQLAlchemy Core로
   OCR Job/AI Job → 문서 uploaded_by → user_consent를 조회한다.
   문서 업로더·AI Job 사용자·SELF profile 소유자가 모두 같은지와 계정 ACTIVE/is_active를 검증한다.
2. 매 검사마다 독립 session/짧은 transaction을 사용한다. 이전 실행 session의 identity map이나
   장기 transaction snapshot을 재사용하지 않고, caller의 처리 transaction을 commit/rollback하지 않는다.
3. `OcrConsentGate`는 매번 동의와 현재 정책 버전을 다시 조회한다. 정책 버전은 호출자가 제공하는
   resolver를 사용하고 fixture의 ocr-consent.v1을 운영 설정으로 하드코딩하지 않는다.
   OCR 전용 과거 버전이 LLM 동의까지 포괄한다는 의미를 부여하지 않는다.
4. `gate.run`은 CLOVA 호출 직전 검사에 사용한다. `ConsentCheckedLlmStructurer`는 같은 Job에
   결속된 gate로 기존 LLM 구조화기 실행 직전 다시 검사한다. 두 위치의 검사 결과를 캐시하지 않는다.
5. 조회 실패·불완전한 timestamp·소유권 불일치를 안전한 내부 사유로 반환한다. DB 원문 예외 chain을
   버리고 취소는 전파한다. 내부 사유는 공개 enum/Worker FailureCode가 아니다.
6. Runtime의 Job별 Gate 조립, OCR_CONSENT_POLICY_VERSION 설정, 안전한 Dispatcher 예외 전달과
   차단 상태 transaction을 연결했다. 이 Worker 단계에는 RLS·DB Trigger·DB 함수가 없다.

Protocol은 실제 SQL 조회와 합성 테스트 대역을 분리하기 위한 하나의 경계다.
독립 transaction의 비용은 외부 호출당 짧은 SELECT 1회이며, 동의 캐시로 대체하지 않는다.

## runtime 연결 및 저장 계약안

[Worker 차단 저장 결정안](../governance/decisions/2026-09-14-ocr-consent-worker-458.md)에
사유별 OCR error_code, STALE/BLOCKED/FAILED 원자 저장, 자동 재시도 없음, commit 후 ACK를 명시했다.
Dispatcher는 동의 사유를 일반 INTERNAL_ERROR로 바꾸지 않는다. lease/event/attempt/token을
확인하고 연결된 Attempt/OCR 변경이 실패하면 전체 transaction을 rollback한다.

CLOVA·LLM 호출 전 검사 외에 결과 반환 전에도 동의를 재검사한다. LLM 비활성 경로에서
CLOVA 중 철회된 경우에도 성공 결과를 저장하지 않는다. Backend 동의 API/접수 Gate와 과거 결과
접근 차단은 같은 #458의 후속 변경으로 구현했다. 상세 route와 사유는
[계약안](../contracts/proposed/ocr-llm-transfer-458.md)에 기록했다.

CLOVA와 LLM 사이 철회 시 이 부품은 성공 결과를 반환하지 않는다. 이미 받은 OCR 원문의 영구
저장/삭제/재노출 정책까지 구현하거나 결정하지 않는다. 검사와 네트워크 전송 사이의 극소 경합을
제거하거나 이미 전송된 외부 요청을 회수한다고 주장하지 않는다.

## 전송 최소화·처방일 경계

기존 전체-token LLM 전송 경로를 차단했다. 안전한 약품명 selector가 검증되기 전에는
LLM 기능을 켜도 외부 호출을 생략하고 로컬 규칙 결과를 반환한다. 생략 여부는 `ocr_job.llm_processing`
컬럼과 OCR 결과 DTO로 전달한다. 실제 데이터를 LLM에 전송할 수 있는 완성본이나 활성화 승인은 아니다.

기존 처방일·처방약 검수 및 확정 흐름은 유지한다. 안전한 전송 범위를 만들지 못한 경우
전체 필드를 로컬 규칙 구조화기로 처리하므로 처방일은 LLM에 보내지 않고 기존 필수 검수를 거친다.
향후 실제 LLM 전송 selector를 도입할 때 날짜 분리·필드 조립·저장 회귀를 다시 검증한다.

## fixture provenance

`tests/fixtures/consent/consent_gate_207_cases.json`은 #465 위 HEAD의 같은 경로를 그대로 가져왔다.
원본을 수정하지 않았으며 병합된 #465와 동일하다. fixture의 간소화 row는 timestamp를
생략하므로 추가 PostgreSQL 테스트에서 granted_at/withdrawn_at 무결성을 별도로 검증한다.

집중 DB 테스트는 최소 물리 컬럼을 전용 임시 schema에 만들고 실제 SELECT와 transaction을
검증한다. #465 migration/전체 제약 검증을 대체하지 않는다. test inventory에 명시적 opt-in으로 등록한다.
