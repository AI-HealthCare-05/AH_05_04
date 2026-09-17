# #668 Check-in 정정 Runtime 권한 검증

- 검증일: 2026-09-17
- 구현 담당: 권가빈. 단일 책임 리뷰어: 송은영.
- 상태: 로컬 구현·검증 완료, 책임 리뷰·CI·운영 적용 대기
- [Decision](../../governance/decisions/2026-09-16-checkin-runtime-lock-668.md) · [Proposed 계약](../../contracts/proposed/checkin-runtime-lock-v1.md)

## 원인과 재현

운영의 읽기 전용 진단에서 Check-in PUT 500의 `permission denied for table safety_assessment`와
세 Track C 테이블의 Runtime SELECT/UPDATE 권한 누락을 확인했다. 실제 사용자 기록과 서버 인증정보는 보관하지 않는다.
격리 PostgreSQL 17/pgvector에 migration 전체와 별도 Runtime/Source Writer 역할을 구성했다.
합성 기록을 UNCONFIRMED → NOT_TAKEN → TAKEN으로 정정할 때 수정 전 동일 권한 오류가 재현됐다.

## 실행 결과

| 검사 | 결과 |
| --- | --- |
| `tests/integration/rag/test_database_role_provisioning.py` | 수정 전 동일 오류로 실패, 수정 후 1 passed |
| `backend/app/tests/medication_checkins`, `track_c`, `medication_schedules`, `medication_reports` | 230 passed |
| `tests/contract/test_database_role_deployment.py`, `test_python_test_inventory.py` | 24 passed |
| `ruff check .`, `ruff format . --check` | 통과 |
| `mypy backend/app ai_worker` | 통과 (772 source files) |
| `scripts/ci/verify_database_head.py --heads-only` | 단일 head `668a1b2c3d4e` |
| `scripts/ci/check_database_logic.py`, `check_protected_table_writes.py` | 통과 |
| `git diff --check` | 통과 |

권한 통합 검증은 관리자 세션으로 합성 fixture만 준비하고 실제 Runtime credential로 서비스 정정을 수행한다.
Track C 이력이 없는 경우와 있는 경우 모두 저장·revision·감사 2건을 확인한다.
ACTIVE Plan만 취소되며 COMPLETED Plan과 Safety/Barrier 내용은 보존된다.
행 잠금 성공, marker 비영값 거부, Runtime의 이력/ID/계획 snapshot 수정 및 INSERT/DELETE/TRUNCATE 거부,
Source Writer 조회 거부를 함께 검증한다. 전체 migration과 재provision 경로도 기존 통합 시나리오에 포함된다.

## 미완료와 적용 순서

전체 CI/coverage, 지정 리뷰어 승인, merge queue, 운영 적용 및 배포 후 화면 검증은 아직 완료하지 않았다.
API/DTO/UI 변경은 없으며 의료 AI 변경이 없어 evals는 적용 대상이 아니다.
운영 실제 기록을 테스트 목적으로 변경하지 않았다. 일정→수정→리포트의 배포 후 확인은 승인된 합성 계정으로 수행한다.

책임 리뷰 승인 후 정상 배포 흐름에서 migration → 역할 provisioning → 앱 기동 순서로 적용한다.
이후 Runtime 잠금/정정과 일정·리포트 일치를 확인한다. downgrade는 사용하지 않으며 문제 시 reviewed forward-fix를 사용한다.
Track C/F 및 Privacy Production 공개 게이트는 이번 수정으로 해제하지 않는다.

## PR #674 담당 리뷰 반영 (2026-09-17)

[담당 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/674#issuecomment-5706249704)의 무조건 downgrade 차단 지적을 반영했다.
항상 0인 marker와 해당 CHECK만 역순으로 제거하며 업무 이력은 건드리지 않는다.
새 회귀는 Safety·Barrier·Plan·Follow-up·Follow-up Audit 합성 이력을 비교하고,
downgrade 후 컬럼/제약 제거와 재upgrade 후 marker 0·제약 복원을 확인한다.

- 전용 PostgreSQL 17/pgvector에서 `tests/migration`과
  `tests/integration/rag/test_knowledge_evidence_index_postgresql.py`: 최초 240 passed, 4 skipped, 2 setup errors.
- setup 오류 2건은 하위 `uv run alembic`이 시스템 Python을 선택해 `asyncpg`를 찾지 못한 환경 문제였다.
  PATH와 UV_PROJECT_ENVIRONMENT를 검증용 가상환경으로 맞춘 뒤
  `tests/migration/test_runtime_bundle_canonical_configuration_migration.py` 전체 재실행: 5 passed.
- 새 `test_checkin_lock_marker_downgrade_preserves_history_and_reupgrade` 단독 실행: 1 passed.
- Ruff check/format, Mypy (774 files), 단일 migration head, DB 로직·보호 테이블 쓰기·test inventory, diff check 통과.
- 이번 재검증은 migration/RAG 회귀 범위이며 전체 CI/coverage나 운영 적용 증거가 아니다.

## 2026-09-17 develop 충돌 해소 검증

- develop의 Retrieval Runtime 권한과 기존 Check-in 잠금 권한·통합 검증을 모두 보존했다.
- 미병합 migration `668a1b2c3d4e`의 부모를 develop head `235a1b2c3d4e`로 연결했다.
  롤백 테스트는 해당 migration의 실제 부모 revision을 읽어 검증한다.
- 전용 pgvector/PostgreSQL 17 합성 컨테이너에서 Track C migration·역할 provisioning 통합 테스트
  17개 통과. Check-in 정정·이력 보존·권한 거부와 Retrieval lifecycle 검증을 함께 실행했다.
- 배포 역할·Python test inventory·migration head 계약 테스트 34개 통과.
- 전체 Ruff lint/format, mypy, 단일 head, DB 로직·보호 테이블 검사와 diff check 통과.
- 전체 CI/coverage와 운영 화면 검증은 이번 로컬 실행에 포함하지 않았다. 운영 적용·공개 승인 상태는 변경하지 않는다.
