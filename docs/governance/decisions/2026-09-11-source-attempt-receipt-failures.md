# PD-362-R2 — Source Attempt 실패 Receipt 보완안

상태: proposed / PR #436 재리뷰 대상. 정현우의 Source 계약 검토 및 송은영의 DB 경계 리뷰 전이다.
사용자의 리뷰 반영 지시에 따라 구현·테스트를 준비했으며 승인 완료로 표시하지 않는다.

문제: 저장 가능한 수집 실패 15종과 Parser 실패가 Receipt 판정에서 누락되어 정상 감사 행을 읽지 못했다.
EMPTY_RESULT 문자열은 수집·정책 계열에 중복되어 저장값만으로 원인을 구분할 수 없었다.

선택한 수정안은 COLLECTION_FAILED 추가다. TIMEOUT 등을 VALIDATION_FAILED에 포함하는 대안은
수집과 검증의 의미를 혼합하므로 선택하지 않았다. failure_code·기존 DB 구조는 유지하고,
EMPTY_RESULT만 기존 validation_reason_code로 계열을 구분한다. 과거 모호한 행은 추정하지 않는다.
전체 규칙은 [제안 계약](../../contracts/proposed/source-attempt-receipt-436.md)에 둔다.

이 변경은 새로운 성공·공개 상태가 아니며 재시도나 Runtime 활성화를 허용하지 않는다.
새로운 DB 로직과 migration 없이 Python에서 구현한다.
