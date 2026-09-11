# Source Attempt Receipt 실패 판정 보완 — PR #436 리뷰안

상태: proposed / 구현 후 담당 리뷰 요청. 승인된 PD-362를 소급 변경하거나 공개 승인을 주장하지 않는다.
구현: 김지혜. Source 계약·decision 의미 검토: 정현우. DB·감사 경계 검토: 송은영.
근거: [#436 MUST FIX 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/436#pullrequestreview-5169535518).
Decision: [PD-362-R2](../../governance/decisions/2026-09-11-source-attempt-receipt-failures.md).

## 판정

`SnapshotAttemptReceipt.decision`에 `COLLECTION_FAILED`를 추가한다.
이는 Provider 요청·수집 단계에서 실패한 실행이며 Parser/Version/정책 검증 실패인
`VALIDATION_FAILED`와 구분한다. 기존 CREATED / NO_CHANGE / SOURCE_VERSION_CONFLICT는 유지한다.
어떤 실패 decision도 Snapshot 선택·공개·자동 재시도 허가가 아니다.

| 기록 계열 | 허용 코드의 정본 | decision |
| --- | --- | --- |
| 수집 | SourceFailureCode 전체 15종 | COLLECTION_FAILED |
| Parser 처리 | IngestionProcessingFailureCode 전체 2종 | VALIDATION_FAILED |
| Source Version | SourceVersionFailureCode 전체 2종 | VALIDATION_FAILED |
| Snapshot 정책 | SnapshotPolicyFailureCode 전체 2종 | VALIDATION_FAILED |
| Version 충돌 | SOURCE_VERSION_CONFLICT | SOURCE_VERSION_CONFLICT |

실패 행은 FAILED 및 snapshot_id=NULL이어야 한다. 성공·NO_CHANGE는 Snapshot 참조와 failure_code=NULL이 필요하다.
코드 정본 enum에서 허용 집합을 가져오며, 네 enum 전 멤버와 고정 vocabulary 테스트로 새 멤버 추가 시
계약·테스트 검토 없이 의미가 조용히 확장되지 않게 한다. 미등록 값·상태 불일치는 고정 오류로 차단한다.

## EMPTY_RESULT 구분

두 계열에 공통인 failure_code는 유지하고 기존 validation_reason_code에 다음 고정값을 기록한다.

- Provider 수집 기록: COLLECTION_EMPTY_RESULT → COLLECTION_FAILED
- Snapshot 정책 기록: SNAPSHOT_POLICY_EMPTY_RESULT → VALIDATION_FAILED

이 구분자는 EMPTY_RESULT에만 허용한다. 다른 failure_code와 조합하면 Receipt 판정을 거부한다.
과거 구분자 없는 EMPTY_RESULT는 원인을 복원할 수 없으므로 decision을 추정하지 않고 Receipt 반환을 거부한다.
원본 Run 감사 행은 삭제·수정하지 않으며 기존 DB 감사 조회로 확인할 수 있다. 임의 backfill은 하지 않는다.
TIMEOUT 등 중복되지 않는 기존 코드와 PARSER_VALIDATION_FAILED는 구분자 없이도 Receipt 조회가 가능하다.

## 적용 경계

새 DB 컬럼·migration·Trigger·RLS·업무 DB 함수 없이 Python 기록/Receipt 경계에서 처리한다.
기존 UPDATE 권한과 transaction은 유지한다. Source Version의 기존 reason 및 원문 대신 hash·길이를 남기는 규칙도 유지한다.
#166 소비자는 새 decision을 명시적으로 처리해야 하며, 이번 PR에서 #166·#165 브랜치를 변경하지 않는다.
