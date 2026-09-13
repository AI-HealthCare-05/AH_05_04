# PD-398-R2: Snapshot 관리 잠금 권한과 provenance 분리

상태: PR #429 후속 수정, 지정 담당 리뷰 대기. 운영 적용·외부 공개 승인 아님.
구현: 김지혜. 검토: 송은영(DB·보안), 정현우(Source·RAG).
근거: [PR #429 현우님 추가 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/429#pullrequestreview-5166896553).

## 문제와 결정

PostgreSQL SELECT FOR UPDATE에는 대상 테이블의 한 컬럼 이상에 UPDATE 권한이 필요하다. 관리 역할에 `verified_at` 권한을 부여한 기존 구현은 API 밖에서 검증 시각과 관리 hash를 감사 없이 바꿀 수 있었다.

`rag_source_snapshot.management_lock_marker` INTEGER NOT NULL DEFAULT 0을 추가하고 일반 CHECK로 0만 허용한다. 관리 역할은 이 컬럼의 UPDATE 권한만 받아 기존 Operation → Snapshot 행 잠금 순서를 유지한다. 표식은 검증 상태·시각·provenance·revision·공개 승인 의미를 갖지 않는다. API 입력에 노출하지 않으며 실제 값을 변경할 수도 없다.

Snapshot 관리 row hash에서 이 표식만 제외해 기존 expected_hash와 감사 before/after hash의 의미를 유지한다. 업무 컬럼은 계속 hash에 포함한다. `verified_at`, `effective_at`, seal 및 identity에는 관리 UPDATE를 부여하지 않는다. Source Writer의 승인된 Python 전이 권한은 유지한다.

## 대안과 적용

기존 provenance/identity 컬럼을 잠금용으로 허용하면 의미 있는 데이터를 바꿀 수 있다. 별도 잠금 테이블이나 advisory lock은 기존 Writer 행 잠금과 다른 경로를 추가하므로 사용하지 않는다. 고정 표식 하나와 CHECK·컬럼 권한으로 기존 transaction 경계를 유지한다. Trigger·RLS·업무 DB 함수는 추가하지 않는다.

기존 migration은 보존하고 `3984b5c6d7e8` forward migration으로 컬럼·제약을 추가한다. migration → head 검증 → 권한 provisioning → 서비스 시작 순서를 따르며, provisioning이 기존 관리 역할의 verified_at 컬럼 권한도 회수한다. downgrade는 거부하고 검토한 forward-fix를 사용한다.

검증은 제한된 관리 로그인으로 행 잠금과 미사용 PENDING 삭제가 성공하고, CURRENT·STALE·FAILED의 verified_at 직접 UPDATE·seal 변경·직접 삭제가 거부되는지 확인한다. 고정 표식의 값 변경도 거부하며 기존 행의 migration 전후 관리 hash가 동일한지 검증한다.
