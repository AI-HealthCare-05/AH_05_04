# #398 구현 진행 및 검증

기준 develop: `bd201c36f3be677558cccba02b27601a8c7ce52b`

상태: 진행 중. Trigger 제거 및 대체 무결성 구현 완료를 의미하지 않는다.

## 금지 원칙

새 DB Trigger·RLS 정의를 추가하지 않는다. 업무 규칙은 Python Service·Repository에 구현하며 일반 FK·UNIQUE·CHECK·최소 권한·row lock을 보조 수단으로 유지한다.

`scripts/ci/check_database_logic.py`는 신규 정의를 검사한다. 기존 10개 migration 파일과 합성 정리 도구 1개 파일만 원래 hash와 정확히 일치할 때 임시 허용한다. 기존 migration은 이력으로 보존하며 최종 DB의 Trigger는 후속 forward migration으로 제거한다. 합성 정리 도구는 Python 전환 후 임시 허용 목록에서 삭제한다. 새 정의를 허용하기 위해 목록을 확장해서는 안 된다.

정적 검사는 동적으로 조립된 모든 SQL을 증명하지 못한다. 최종 migration과 도구용 스키마 적용 후 PostgreSQL 카탈로그에서 사용자 정의 Trigger 및 RLS 정책·활성화를 검사하는 통합 검증을 반드시 추가한다.

## 제거 대상과 대체 경로

| 영역 | 기존 정의 | Python 대체 및 검증 |
| --- | --- | --- |
| Source Snapshot·Artifact·Verification | 164f, 165a, 165c, 165d, 165e migration | `sqlalchemy_source_snapshot_repository.py`의 DB 함수 호출을 operation→snapshot 잠금, 상태·승인·시각 검증, 감사 INSERT와 조건부 UPDATE로 전환. Writer 권한 구성과 함께 적용 |
| Prescription Version | 169a migration | `prescription_repository.py`와 `prescriptions.py`에서 조립 검증·count/hash·멱등성·원자적 활성화. 슬롯 제약 및 공통 소비 검증 추가 |
| Candidate Search | 171c migration | `medication_candidate_repository.py`의 모든 결과 변경이 부모 잠금을 공유하고 DB 저장 결과를 집계한 뒤 검증하도록 보강 |
| Runtime Transition | 164b migration | 승인된 전이 Service와 Repository의 revision 검사·원자적 pointer 변경, 보호 이력 권한 회수 |
| Evidence/Citation | 164c migration | provenance 제약을 유지하고 기존 이력 UPDATE·DELETE 권한 제한 및 승인된 Python 저장 경로 검증 |
| Check-in Audit | 201a migration (#402) | 감사 append-only를 권한으로 보호하고 Check-in 변경과 감사 저장의 동일 transaction·rollback 유지 |
| 합성 정리 도구 | `synthetic_control.sql` | 리뷰·철회·감사·실행 코드와 명시적 transaction/잠금으로 전환. 전용 definer 함수 호출 제거 |
| Catalog (#166 미병합) | 별도 작업 브랜치 | Identity 명시 결속·Set/hash 검증과 FK/UNIQUE/NOT NULL. 새 Trigger 생성 코드 이식 금지 |

## 수행한 작업

- [x] 최신 develop에서 독립 작업 브랜치 생성
- [x] 기존 정의 파일 hash 고정 및 제거 목록 수집
- [x] 신규 Trigger·RLS 정적 검사 및 CI 연결
- [x] 저장소 AGENTS.md에 재도입 금지 원칙 기록
- [x] 여러 줄·주석 분리 정의, 기존 파일 변경 우회, 제거 구문 허용 테스트
- [x] Candidate 결과 추가·최종화 시 부모 잠금 및 RUNNING 상태 재검증
- [x] Candidate 최종화에서 실제 저장된 전체 결과·표시 결과 집계 검증
- [ ] Python 대체 저장 경로 및 계약 구현
  - Snapshot DB 함수 호출을 Python 잠금·전이 검증·조건부 UPDATE·감사 INSERT로 대체
  - 상태 변경과 선택 감사의 savepoint rollback 추가
  - 작업자 없는 CURRENT 선택 차단 및 PD-398 제안 계약 작성
  - 아직 Writer 권한·제거 migration 연결 전으로 이 브랜치 배포 금지
- [ ] 실제 Runtime/Writer 권한·프로세스·배포 구성
- [ ] 기존 데이터 검증과 forward migration
- [ ] 최종 스키마 검사·PostgreSQL 통합 및 회귀 검증

아직 운영 DB나 배포 설정을 변경하지 않았으며 기존 Trigger를 제거하지 않았다. 대체 경로와 권한이 준비되기 전에는 제거 migration을 실행하지 않는다.

## 초기 검증

- 재도입 방지·Candidate 무결성 단위 검증: 5 passed
- 독립 PostgreSQL 17의 Candidate Repository·Identification Service·Candidate Service: 43 passed
- 독립 PostgreSQL 17의 동시 확정·거절 및 멱등 재시도: 5 passed
- 변경 Python Ruff 검사 및 Candidate Repository Mypy 통과

Candidate 검증은 아직 전체 Trigger 대체 완료를 의미하지 않는다. 임의 직접 DML 차단, 최종 migration, 감사 및 Writer 권한은 남아 있다.

## Snapshot Python 전이 검증

- Snapshot 전이/Repository 단위 검증 76 passed (아래 302개에 포함)
- Source ingestion 회귀 302 passed
- Trigger·전용 함수 설치 없는 독립 PostgreSQL Snapshot lifecycle 검증 14 passed
- 감사 INSERT 실패를 호출자가 잡고 외부 transaction을 commit해도 PENDING 상태와 감사 부재 유지 확인
- 변경 Python Ruff·format 및 생산 코드 Mypy 통과
- Source ingestion 전체 수집은 기존 실행 환경의 boto3 부재로 실패. S3 관련 2개 모듈은 제외했으며 해당 테스트 통과를 주장하지 않는다.

브랜치 생성 reflog: `bd201c3 branch: Created from origin/develop`. #362 작업 브랜치에서 분기하지 않았다. #362 미병합 정책과 통합할 때는 별도로 최신 develop 및 해당 PR의 병합 여부를 확인한다.
