# #458 Worker 동의 조회·호출 직전 검사 구현 단위

- 구현 담당: 김지혜. 담당 리뷰어: 송은영(Backend/Security·Worker 동의 조회 경계).
- Privacy/Product 정책 증빙: 권가빈 답변 별도. Frontend 소비 계약: 남한솔 답변 별도.
- 기준: develop `ac1b148a`, #465 수정 HEAD `6d5494cc0a488675a195cd5db71bd5e50a49fd82`.
- 상태: 로컬 구현. #465 미병합 스키마 기준의 부품이며 기본 runtime 조립·API·공개 활성화 미연결.

## 2026-09-14 은영 답변 반영

사용자 제공 Discord 10:46 답변 기준이며 원문 URL은 아직 제공되지 않았다.

- #207/#465는 migration/model/repository/공통 fixture 인계 기준이다. 동의 API와 실제 Gate 연결은 후속.
- Backend 접수 전 검사, Worker CLOVA 직전 및 LLM 직전 재검사로 분담한다.
- row 없음/철회/policy mismatch/조회 실패는 외부 호출을 차단한다.
- 접수 전 미동의·철회·버전 불일치는 CONSENT_REQUIRED 계열이다.
- 접수 후 철회는 BLOCKED + STALE, OCR FAILED + CONSENT_WITHDRAWN 방향이며 실제 저장 경로는 #458에서 확정한다.
- 유효 동의·LLM만 생략된 경우 기존 OCR 검수·수정 흐름 재사용 방향이다.
- 완전 수동 입력 저장 경로는 은영이 추가 확인한다. 철회 후 결과 노출·검수는 가빈 정책 확인 대기.
- 사용자 확인: 은영 추가 질문과 가빈 정책 질문은 발송 완료. 한솔 추가 질문 없음.

## 구현

1. `SqlAlchemyOcrConsentRepository`는 Backend ORM import 없이 SQLAlchemy Core로
   OCR Job/AI Job → 문서 uploaded_by → user_consent를 조회한다.
   문서 profile의 SELF/user 일치와 계정 ACTIVE/is_active를 애플리케이션에서 검증한다.
2. 매 검사마다 독립 session/짧은 transaction을 사용한다. 이전 실행 session의 identity map이나
   장기 transaction snapshot을 재사용하지 않고, caller의 처리 transaction을 commit/rollback하지 않는다.
3. `OcrConsentGate`는 매번 동의와 현재 정책 버전을 다시 조회한다. 정책 버전은 호출자가 제공하는
   resolver를 사용하고 fixture의 ocr-consent.v1을 운영 설정으로 하드코딩하지 않는다.
   OCR 전용 과거 버전이 LLM 동의까지 포괄한다는 의미를 부여하지 않는다.
4. `gate.run`은 CLOVA 호출 직전 검사에 사용한다. `ConsentCheckedLlmStructurer`는 같은 Job에
   결속된 gate로 기존 LLM 구조화기 실행 직전 다시 검사한다. 두 위치의 검사 결과를 캐시하지 않는다.
5. 조회 실패·불완전한 timestamp·소유권 불일치를 안전한 내부 사유로 반환한다. DB 원문 예외 chain을
   버리고 취소는 전파한다. 내부 사유는 공개 enum/Worker FailureCode가 아니다.
6. RLS·DB Trigger·DB 함수·migration·설정·공개 API·Frontend 변경은 없다.

Protocol은 실제 SQL 조회와 합성 테스트 대역을 분리하기 위한 하나의 경계다.
독립 transaction의 비용은 외부 호출당 짧은 SELECT 1회이며, 동의 캐시로 대체하지 않는다.

## runtime 미연결 이유와 다음 연결 조건

현재 Dispatcher는 알 수 없는 예외를 INTERNAL_ERROR로 변환한다. OcrConsentDeniedError를
기존 runtime에 그대로 넣으면 철회가 잘못 분류되므로 이번 부품을 기본 runtime에 등록하지 않는다.

다음 구현에서는 지정 리뷰어가 검토할 #458 저장 계약과 함께 다음을 연결해야 한다.

- 철회 시 현재 lease·fencing token을 확인한 Attempt BLOCKED / Job STALE / OCR FAILED +
  CONSENT_WITHDRAWN의 원자 저장, 성공 결과 저장 차단 및 ACK 순서.
- 조회 실패·실행 직전 미동의/버전 불일치의 종료·감사·재시도 구체 처리.
- 현재 정책 버전 resolver 및 OCR 목적의 외부 LLM 고지/재동의 범위.
- #465 최종 schema·fixture 변경 재대조 및 실제 migration 환경 검증.
- Backend 동의 API와 접수 전 Gate는 은영 담당 범위와 연결.

CLOVA와 LLM 사이 철회 시 이 부품은 성공 결과를 반환하지 않는다. 이미 받은 OCR 원문의 영구
저장/삭제/재노출 정책까지 구현하거나 결정하지 않는다. 검사와 네트워크 전송 사이의 극소 경합을
제거하거나 이미 전송된 외부 요청을 회수한다고 주장하지 않는다.

## 전송 최소화·처방일 경계

이번 단위는 payload selector/LLM 출력 schema를 변경하지 않는다. 기존 전체-token 전송 경로의
최소화가 완료됐다는 의미가 아니다. 기본 LLM 설정과 공개 gate를 변경하지 않는다.

기존 처방일·처방약 검수 및 확정 흐름은 유지한다. 현재 ClovaOcrEngine은 선택된 구조화기의
fields를 최종 결과로 사용한다. 처방일 token만 제외하면 규칙 기반 날짜가 자동 병합되는 구조가
아니므로, 날짜 분리·필드 조립·저장 회귀는 다음 최소화 구현에서 검증한다. 날짜 전송 제외를
승인된 정책으로 쓰거나 한솔에게 필수항목 여부를 다시 질문하지 않는다.

## fixture provenance

`tests/fixtures/consent/consent_gate_207_cases.json`은 #465 위 HEAD의 같은 경로를 그대로 가져왔다.
원본을 수정하지 않았으며 #465 병합 후 동일 파일과 비교한다. fixture의 간소화 row는 timestamp를
생략하므로 추가 PostgreSQL 테스트에서 granted_at/withdrawn_at 무결성을 별도로 검증한다.

집중 DB 테스트는 최소 물리 컬럼을 전용 임시 schema에 만들고 실제 SELECT와 transaction을
검증한다. #465 migration/전체 제약 검증을 대체하지 않는다. test inventory에 명시적 opt-in으로 등록한다.
