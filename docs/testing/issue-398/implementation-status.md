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
  - Source 역할 정책 함수 및 서로 다른 실제 로그인 credential 통합 테스트 구현
  - Runtime 직접 쓰기/SET ROLE, Writer 감사 변경, 신규 테이블 쓰기 차단 확인
  - 별도 Writer 명령·opt-in Compose 서비스 구현. 초기화 스크립트·운영 배포 전환은 미완료
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

## Source Writer 일회성 실행 경로

- 별도 credential만 받는 Snapshot 선택 명령과 opt-in Compose 서비스 추가
- 일반 배포에서는 `source-admin` profile 서비스를 시작하지 않음
- 잠금 후 예상 checksum 확인, 상태 변경과 사유 코드 감사의 단일 transaction
- 재실행 시 중복 감사 방지, 감사 실패 시 전체 rollback
- 전용 환경 변수·secret 혼합 차단·Compose 경계 단위 검증: 12 passed
- Snapshot lifecycle PostgreSQL 검증: 17 passed (기존 14 + 새 Writer 시나리오 3)
- Ruff·Mypy·재도입 검사·diff 검사 통과
- 실제 제한 역할로 실행 함수 검증 완료. 컨테이너 CLI 실행, 역할 provisioning 및 migration/bootstrap 연결은 남아 있음


## 실제 Writer 실행 및 권한 보강 검증

- Writer 명령의 DB 실행 함수를 실제 별도 로그인으로 실행: 선택·멱등 재실행 성공
- Runtime 로그인 쓰기 차단, 관리자 로그인 실행 거부
- 감사 INSERT 권한 제거 시 Snapshot 상태 전체 rollback, 권한 복구 후 재시도 성공
- 전역 default grant가 남아 있으면 권한 전환 거부 (schema-local REVOKE 우회 방지)
- 관련 단위·PostgreSQL 테스트 합계 31 passed (lifecycle 18 + 역할 정책 1 + Writer 설정 12)
- Ruff·생산 코드 Mypy 통과

## Prescription 저장 경계 1단계

- Repository의 최소 1개·정수 1..N 슬롯 검증 및 실제 저장 구성 재확인
- 새 버전 생성 전 부모 잠금, 버전·약·포인터의 savepoint rollback
- 잘못된 슬롯 입력 및 약 INSERT 실패 후 호출자 commit에도 기존 버전 유지 검증
- Repository·Service·확정 동시성 테스트 20 passed
- count/hash 저장·소비 검증, 슬롯 DB 제약, 멱등 키, 권한·제거 migration은 후속 단계

## Prescription 내용 hash 2단계

- 공통 prescription-content@1 fingerprint 구현, 최소 구성 및 숫자·문자열 검증
- 입력과 실제 DB 저장 내용의 count/hash 대조를 savepoint 안에서 수행
- 정정 DTO의 순서 누락을 입력 단계에서 차단
- hash·DTO·Repository·Service·확정 동시성 검증 39 passed
- hash 영구 저장·소비 경로·DB 슬롯 제약·권한·migration은 미완료

## Source·Catalog 저장 경계 1단계

- Worker와 Backend Source/Catalog Repository의 검증 이력 INSERT가 상태 전환과 동일한 Operation 잠금 사용
- 실제 별도 연결에서 전환 잠금 중 승인 INSERT가 대기하고 timeout 시 감사가 남지 않는 두 경로 검증
- Source lifecycle·Repository 단위·기존 Catalog 회귀 합계 135 passed
- 현재 Catalog build는 source identity 연결·export 검증 후 save_build 포트를 호출하지만, 실제 DB build adapter는 현재 브랜치에 없음. Catalog 기존 검증 통과를 DB 저장 전환 완료로 해석하지 않는다.
- 미병합 #404 코드는 가져오지 않음. Catalog DB adapter·Set/hash 저장 및 Source 관리 권한의 운영 연결은 남아 있음.
