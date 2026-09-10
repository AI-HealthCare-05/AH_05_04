# PD-165 제품 reject 계약 v1 — 구현 리뷰안

상태: proposed / 담당 리뷰 전. 승인 또는 Production 적용 근거가 아니다.
사용자 지시로 구현·검증 후 PR 리뷰를 진행한다. 현우님 의견은 제공된 v2에 반영되었으며
은영님은 DB·무결성, 가빈님은 제품·Safety 경계를 리뷰한다.

## 결정 제안과 이유

필수 ITEM_SEQ의 누락·타입 오류·중복은 제품 Identity를 만들 수 없는 오류다.
Source의 양수 거부 한도와 관계없이 FAILED / PARSER_VALIDATION_FAILED로 기록하고 Snapshot은 만들지 않는다.
Hard Limit 수치·계산을 바꾸지 않고, 그 전에 거부하는 오류 부류를 명시한다.
새로운 failure_code는 도입하지 않는다. 미등록 reject 코드·미지원 버전·다른 Operation도 동일하게 실패한다.

Run에는 nullable reject_code_contract_version을 추가한다. #436 attempted_canonical_contract는
checksum 이전 실패에는 생성할 수 없고 기존 필드 계약도 고정돼 있으므로 버전 전용 컬럼이 필요하다.
과거 NULL을 추정하지 않고 컬럼별 lifecycle UPDATE 권한에 새 provenance 필드를 추가하지 않는다.
버전 이력이 존재하면 downgrade를 거부한다. 새 Trigger/RLS/업무 DB 함수는 없다.

## 실제 실행 경계

`ingest_and_persist_product_run`이 전체 Raw Artifact 검증 → 2-pass 판정 → Run/Artifact 또는 Snapshot을 연결한다.
호출자가 transaction을 소유하며 저장 실패는 전파하여 rollback한다. 파일은 content-addressed 기존 저장소에
남고 #347의 미참조 파일 reconciliation 및 재시도 멱등 저장을 따른다. 임의 즉시 삭제는 하지 않는다.

기존 클라이언트의 기본키 실패는 SCHEMA_DRIFT와 전체 통계만 반환하고 pages는 비운다.
이 경우 전체 Raw Artifact의 checksum·페이지·본문·총건수·Receipt를 재검증해 실패 감사 입력만 복원한다.
원래 SourceRunResult는 변경하거나 노출하지 않는다. 누락 파일·부분 페이지·다른 failure는 복원하지 않는다.
실패 PK 증빙을 성공 증빙으로 바꾸지 않으며 실제 오류가 없어도 Snapshot 적격성은 회복하지 않는다.

새 실행은 `mfds-product-reject-parser@1`과 `source-reject-codes@1`을 함께 명시한다.
기존 내부 저장 함수는 과거 parser/version 호환 입력을 유지하지만, 버전 없는 REJECTS 신규 저장은 차단한다.
이 계약 적용 실행은 새 orchestration을 사용해야 하며, 버전 없는 이전 실행을 소급 승인하지 않는다.
자동 수집 스케줄러·외부 API 활성화·#166 소비·사용자 입력 정규화·Runtime 공개는 범위 밖이다.

정본: [proposed 계약](../../contracts/proposed/post-mvp-1/source-reject-codes-v1.md).
