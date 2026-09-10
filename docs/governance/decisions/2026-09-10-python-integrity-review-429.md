# PD-398-R1: PR #429 무결성 리뷰 보완

상태: 작업 브랜치 구현·검증, 담당 리뷰 승인 대기. 운영 배포 승인 아님.
구현: 김지혜. 검토: 송은영(DB·보안), 정현우(Source·RAG), 권가빈(처방·제품).
근거: Issue #398 및 PR #429 리뷰. DB Trigger·RLS·업무 저장 함수는 재도입하지 않는다.

## 처방 확정·정정의 성공 응답 재현

리뷰에서 지적한 동일 요청 재시도 409를 공통 SYNC_MUTATION 멱등성 경계로 해결한다. 기존 요청 DTO·필수 헤더를 유지하기 위해 문서와 정정 기준 버전의 자연 키를 사용한다. 소유권을 재확인한 뒤 DB UNIQUE, 요청 지문, 암호화된 응답 snapshot을 사용하며 도메인 변경과 함께 commit한다. 동일 키의 다른 정정 내용은 409, 같은 요청은 최초 201/200을 재현한다. 보존기간 이후에는 오래된 상태를 임의 복원하지 않는다.

키 구성·TTL·오류·최초 응답 의미의 상세 계약은 [Python Prescription 무결성](../../contracts/proposed/python-prescription-integrity-398.md#확정정정-요청-멱등성-pd-398-r1)을 따른다. 해당 계약과 자동 테스트는 본 PR의 변경이며, 승인은 지정 담당 리뷰어가 수행한다.

## Snapshot SQL 삭제 방어와 #404 통합

검증된 Snapshot은 불변 Verification 이력과 양방향 일반 FK·CHECK로 연결한다. 기존 PENDING 삭제 경로를 유지하면서 검증된 행의 관리 역할 직접 DELETE를 거부한다. 자세한 컬럼·전이·backfill·역할 범위는 [Source 전이 계약](../../contracts/proposed/python-snapshot-transition-398.md#pr-429-검증된-snapshot-직접-삭제-방어)에 명시한다. 이는 권한 상승 함수 도입 제안 대신 선택한 구현이다.

#404는 2026-09-10 병합된 최종본을 포함한다. 새로운 Trigger 정의는 없으며 Python 인증 동작을 보존하고 인증 테이블의 Runtime 권한만 최소 컬럼으로 연결한다. `39818293a4b5`로 두 migration head를 연결하고 `398293a4b5c6`에서 Snapshot seal을 추가한다. 기존 적용 migration은 변경하지 않는다.

## 기존 Source 결정과 #386 참조 정렬

[2026-09-08 Source DB-owned 결정](2026-09-08-source-snapshot-db-transition.md)을 superseded로 명시한다. 이미 적용된 migration 파일은 역사적 이력으로 보존하며 신규 정의의 예외 근거로 사용하지 않는다. 이를 승인 선례로 인용하던 [protected retrieval 결정](2026-09-09-protected-retrieval-runner-access-control-candidate.md) §4·§14는 Python 검증·권한 분리·일반 제약과 별도 담당 리뷰 원칙으로 정렬한다. 이는 #386의 미구현 protected 기능 전체를 이번 PR에서 구현했다는 뜻이 아니다.

## 검증 및 배포 설명의 범위

SQL 문자열/AST 기반 CI 검사는 휴리스틱이다. 동적 SQL·간접 호출 전부를 증명하지 않으며 역할·credential 분리, 실제 제한 로그인 테스트와 migration 이후 DB 카탈로그 검증을 함께 사용한다. 배포 스크립트의 실행 순서는 migration → verify-db-head → provision-db-roles → 서비스 시작이다. 권한 부여 전에 잔여 Trigger/RLS/제거 함수와 실제 head를 검사하며 실패하면 시작을 중단한다. 운영 DB 적용은 본 로컬 검증에 포함하지 않는다.
