# #192 C1 저장 기반 검증

- 기준: `develop` `0ea12641` 반영 후 Draft PR #310 로컬 구현
- 대상 migration: `192a1b2c3d4e` (부모 `e8c41a09d652`)
- 상태: 로컬 검증 통과 (2026-09-13). 원격 CI·담당자 승인·운영 적용과 구분한다.
- PostgreSQL 17 / Redis 7의 전용 격리 환경과 합성 레코드만 사용한다.

## 집중 검증

`tests/migration/test_track_c_storage_migration.py`는 각 테스트마다 별도 DB를 만들고 제거한다.
다른 migration 테스트의 이력 복원 DB나 사용자의 개발 DB를 변경하지 않는다.

- fresh upgrade, 부모 head의 기존 Check-in 데이터 보존 upgrade, 반복 upgrade
- 빈 DB downgrade/재upgrade, 이력 존재 시 전체 중단과 데이터 보존
- ORM 필드·CHECK·unique·FK와 실제 PostgreSQL 구조 대조
- 잘못된 Safety 결과 조합/JSON 모양/revision/없는 부모 거부
- 다른 Check-in 또는 다른 revision의 Safety와 Barrier 결속 거부
- DECLINED와 미응답 구분, 허용하지 않는 Barrier 코드 거부
- ACTIVE Plan 동시 생성 경쟁에서 하나만 commit, 종료 이력 보존
- Follow-up unique·정정 audit revision 검사와 한 transaction의 rollback
- SELF 소유권 조회, 타인/없는 ID 결과 은닉
- 과거 Safety revision 보존과 Check-in revision별 새 이력
- 신규 5개 테이블의 사용자 Trigger·RLS 부재

정정 audit 테스트는 **저장 형태와 transaction 원자성** 검증이다.
실제 Follow-up mutation/HTTP 멱등 재전송/상태별 제출 허용 정책 검증을 대신하지 않는다.
코드 seed의 `synthetic-v1`, 빈 config 객체는 저장 fixture이며 승인된 HandlerConfig가 아니다.

## 후속 검증

#193~#195에서 실제 Application Service/Repository 쓰기와 잠금 순서,
NOT_TAKEN·현재 revision·최신 ROUTINE 판정, append-only 이력·종료 Plan 재활성화 차단,
follow-up 감사 원자 저장·SYNC_MUTATION 재전송, Track B 정정 무효화를 연결해 검증해야 한다.
HandlerConfig 상세·운영 seed·Track C 공개는 이 테스트 결과로 승인하지 않는다.

## 최초 구현 로컬 결과 — `bfbc7008`

| 검사 | 결과 |
| --- | --- |
| `bash scripts/ci/run_test.sh` | exit 0 |
| Migration 전체 | 223 passed, 4 skipped |
| Backend·Contract·PostgreSQL | 1949 passed, 85 skipped |
| Redis 통합 | 24 passed |
| Worker | 3077 passed, 8 skipped |
| 합산 Coverage | 92% |
| Ruff check / format | 통과 |
| Mypy Backend·Worker | 595 source files 통과 |
| Test inventory / 단일 Alembic head / DB 로직 재도입·보호 쓰기 검사 | 통과 |
| 문서 링크·Markdown 구조·diff 검사 | 통과 |

새 C1 집중 검증은 위 Migration 전체에 포함된 13건이며 별도로 합산하지 않는다.
최종 migration head는 `192a1b2c3d4e`, 사용자 Trigger·RLS·제거 대상 함수는 0개다.

최초 전체 실행은 전용 가상환경의 Worker 의존성 `boto3` 설치 누락으로 수집 오류 2건이 났다.
`uv sync --group app --group dev --group worker`로 기존 lockfile 의존성을 채운 뒤,
전용 임시 DB를 새로 만들고 전체 스크립트를 다시 실행한 **최종 성공 결과**를 위에 기록했다.
저장소 의존성·Worker 코드·공개 설정은 변경하지 않았다.

## #477 병합 후 develop 정렬

`develop` `f10ca016`을 반영하고 README의 D-04·C1 항목을 모두 보존했다.
미병합 #192 migration의 부모만 `e8c41a09d652`에서 `166f30415263`으로 변경했다.
#192 테이블·제약 정의는 변경하지 않았다. 위 전체 테스트 수치는 이전 `bfbc7008`의 결과이며
이번 병합 결과에서 전체 스크립트를 재실행한 수치로 해석하지 않는다.

재연결 집중 검증: C1 PostgreSQL 13 passed. 단일 Alembic head, 신규 DB 로직 재도입·보호 쓰기 검사, Ruff·format·diff 검사 통과.
