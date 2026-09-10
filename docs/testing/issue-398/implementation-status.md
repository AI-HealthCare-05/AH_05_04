# #398 구현 진행 및 검증

기준 develop: `99bb2597afb0b0b2d4514bf44b5e7b9f66cd6643`

상태: #398 로컬 코드·검증 완료. 팀 리뷰와 #404 병합 후 통합 대기.

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
- [x] Python 대체 저장 경로 및 계약 구현
  - Snapshot DB 함수 호출을 Python 잠금·전이 검증·조건부 UPDATE·감사 INSERT로 대체
  - 상태 변경과 선택 감사의 savepoint rollback 추가
  - 작업자 없는 CURRENT 선택 차단 및 PD-398 제안 계약 작성
  - Writer 권한과 Source·감사·처방·Candidate 제거 migration 연결 완료
- [x] 실제 Runtime/Writer 권한·프로세스·배포 구성
  - Source 역할 정책 함수 및 서로 다른 실제 로그인 credential 통합 테스트 구현
  - Runtime 직접 쓰기/SET ROLE, Writer 감사 변경, 신규 테이블 쓰기 차단 확인
  - 별도 Writer 명령·opt-in Compose 서비스와 배포 전 역할 provisioning 구현
- [x] 애플리케이션 public 스키마의 기존 데이터 검증과 forward migration
- [ ] 합성 정리 도구 전환 후 최종 스키마 검사·PostgreSQL 통합 및 회귀 검증

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

## Candidate 저장 경계 2단계

- 최종화 Service가 결과 INSERT·실제 집계·최종 상태 UPDATE를 단일 Repository savepoint로 호출
- 최종화 실패 후 호출자 commit에도 부분 결과 없음, 재시도 성공 및 완료 후 중복 결과 추가 차단
- Candidate Repository·Identification Service·최종화 단위 검증 31 passed
- Ruff·생산 코드 Mypy·Trigger/RLS 재도입 검사 통과

## 이전 재개 지점 — 398a/398b 적용 전 기록

이번 단계는 세 영역의 기초 저장 검증 보강이며 영역 전체 완료가 아니다.

1. Prescription: `prescription-content@1`은 입력과 저장 직후 대조에 연결됨. 아직 부모 medication_count/content_hash 저장, 자식 count 결속·1..N 슬롯 DB 제약, 기존 데이터 검사·backfill, 모든 소비자의 검증 연결, DB 멱등 키가 필요함. 적용된 과거 migration은 변경하지 않고 forward migration으로 전환할 것. 기존 assembly_xid는 아직 존재함.
2. Source·Catalog: 상태 전이·검증 이력은 Operation 잠금 공유. Source의 나머지 쓰기/승인/철회 관리 권한과 배포 연결 필요. Catalog의 build/save_build 포트·export 검증은 있으나 실제 DB build adapter 미연결. identity 및 Set/hash 계약을 정렬한 뒤 원자 저장 구현 필요. 작업 중인 #404를 가져오거나 수정하지 말고 머지 후 통합할 것.
3. Candidate: Service의 결과 조립·최종화 원자성 구현. 직접 DML 권한 제한과 모든 쓰기 경로 잠금·제거 migration 이후 회귀 검증 필요.
4. 공통: 기존 DB Trigger 제거와 Runtime/Writer 권한·bootstrap·배포 전환은 미완료. 이 상태로 배포 가능 판정 금지. #402 Check-in 감사도 제거 목록에 유지. 다른 영역을 제외하거나 전체 완료로 표시하지 말 것.

검증은 독립 PostgreSQL 테스트 DB에서 수행했으며 운영 DB는 변경하지 않았다.

## Prescription fingerprint 확장 migration

- 398a: 부모 count/hash, 약 count, 복합 FK·슬롯 CHECK·부모 UNIQUE 추가
- Repository 신규 저장에 count/hash와 약 count 명시
- 전체 기존 migration 이력으로 만든 폐기 DB에서 빈 DB·기존 데이터 backfill·오류 데이터 rollback 검증
- 기존 Trigger의 원래 활성 상태 복구 및 확장 단계 downgrade 확인
- 아직 nullable 확장 단계이며 NOT NULL 강화·소비 검증·권한·Trigger 제거는 남아 있음
- migration·Repository·Service·hash 관련 33 passed. count 불일치 FK, 슬롯 범위 CHECK, 중복 슬롯 UNIQUE의 실제 거부 확인

## Prescription 응답 소비 검증

- 공통 저장 fingerprint verifier를 확정·정정·상세·최신 처방 응답에 연결
- 내용 변경, 자식 count 누락, 부모 metadata 누락, 약 행 삭제 시 409 사용 불가로 차단
- Service 및 확정·정정 API 회귀 42 passed
- 다른 Candidate/Guide/Chat/일정/Worker 직접 소비 경로 연결은 남아 있음

## 소비 경로 연결 및 NOT NULL 강화

- 398b: 기존 데이터 전체 count/hash 재검증 후 부모 count/hash·자식 count NOT NULL
- 공통 DB 소비 검증을 Prescription·Candidate·Guide·Chat·일정에 연결
- 버전 metadata와 실제 약 목록을 함께 검증하며 NULL·내용 변경·약 누락 차단
- 테스트 fixture도 완성된 목록의 실제 hash를 계산하도록 변경. 운영 fallback/default hash 없음
- 기존 nullable 확장/소비 일부 미연결에 관한 위 기록은 당시 단계의 이력이며 현재는 이 단계로 대체됨
- 기존 Trigger 제거·assembly_xid 제거·배포 권한은 아직 미완료

검증 결과:

- 전체 Backend: 1257 passed, 2 skipped
- 전체 migration: 150 passed
- 소비 경로·API·동시성 선별 검증: 134 passed (일부는 위 Backend/migration과 중복)
- 변조·약 누락 소비 차단 14개 및 NOT NULL DB 거부 3개는 전체 Backend 결과에 포함
- 변경 Python Ruff/format, 생산 코드 Mypy, Trigger/RLS 재도입 검사, diff 검사 통과
- 과거 Trigger 계약 테스트는 #398 이전 schema에서 유지하고 최신 migration은 전체 이력 위에서 별도 검증

다음 남은 작업: 요청 멱등성 확장, 불변 테이블 최소 권한·실행 계정/배포 연결, assembly_xid·Trigger 제거. 이 작업의 소비 경로 연결 및 NOT NULL 강화는 완료했으며 운영 migration은 실행하지 않았다.

## Source 권한 전환 사전 보강 — 컬럼 ACL 및 복제 권한

- 테이블 REVOKE 이후에도 컬럼 INSERT/UPDATE/REFERENCES 권한이 남는 PostgreSQL 경로 차단
- Source의 PUBLIC/Runtime/Writer 컬럼 ACL 및 Writer가 다른 테이블에 직접 받은 컬럼 ACL 회수
- Runtime/Writer provisioning과 Writer 실행 시 REPLICATION 역할 거부
- 실제 별도 로그인으로 정책 재실행, 감사 변경 차단, 다른 테이블 컬럼 쓰기 차단, 정상 선택 및 rollback 검증
- 관련 Source lifecycle·역할·Writer 테스트 33 passed, 변경 파일 Ruff/format 및 생산 코드 2개 Mypy 통과
- Trigger/RLS 재도입 검사와 diff 검사 통과

이번 단계는 권한 정책의 우회 경로 보강이다. 기존 Trigger는 아직 제거하지 않았고 운영 DB도 변경하지 않았다. 배포 bootstrap의 광범위 DML/default grant와 전용 Writer provisioning을 먼저 정렬해야 제거 migration을 안전하게 연결할 수 있다. #404는 머지 후 통합 대상으로 유지한다.

## 배포 bootstrap 및 전용 Writer 권한 연결

- Bootstrap의 전체 테이블 DML, 전이 함수 EXECUTE, 신규 테이블·sequence 자동 grant 제거
- Admin/Migration/Runtime/Writer 이름 충돌 차단, Writer 로그인 별도 생성 및 Migration 기본 권한 회수
- migration 뒤 `provision-db-roles` 일회성 관리자 컨테이너 연결. 실패 시 API·Worker 시작 전 배포 중단
- Runtime의 명시적 테이블 목록, Prescription 버전·Check-in/Evidence SELECT/INSERT, Source 별도 Writer 정책 적용
- 기존 PUBLIC/실행 계정 테이블·컬럼·sequence 권한 회수 후 단일 transaction으로 재구성
- 신규·미등록 테이블 차단, 허용 테이블 종속 sequence만 USAGE/SELECT
- Source Writer 비밀번호는 PostgreSQL bootstrap 및 별도 Source Writer에만 주입; API/일반 Worker/migrate와 분리
- migration 전 Source Writer도 중지 확인. 기존 일반 Worker 배포 제외 선택 유지
- psql 17에서 역할 이름 충돌 시 성공 종료하지 않고 ON_ERROR_STOP으로 실패하는지 확인

검증: Source/Writer/실제 bootstrap·provisioning 34 passed, 배포 계약·입력 검증 29 passed, 과거 Source migration 계약 4 passed (총 67개, 재실행 제외). Ruff/format, 생산 코드 Mypy, shell 구문, Trigger/RLS 재도입 검사, diff 검사 통과. 현재 ORM의 모든 테이블과 명시적 목록 대조 완료.

폐기 DB에서 bootstrap → provisioning → 재배포 반복과 계정별 실제 DML/sequence 거부, 실패 rollback을 검증했다. 전체 migration 이력에는 아직 기존 Source 전이 함수가 남아 있으므로 provisioning이 의도대로 거부되는 것도 확인했다. 기존 Trigger를 제거하지 않았고, 운영 배포·컨테이너 이미지 빌드/실행·운영 DB 변경은 하지 않았다. 다른 도메인의 명시적 호환 DML 목록은 해당 도메인 Writer 전환 완료를 의미하지 않는다. 제거 migration과 도메인별 남은 전환, #404 병합 후 통합이 필요하다.

## 398c Source 함수·트리거 제거

- 398c3d4e5f60 forward migration: Source 트리거 6개, 함수 5개 제거. 적용된 과거 migration은 변경하지 않음
- Source 7개 테이블 잠금 및 소유자 외 테이블·컬럼 권한 회수를 제거와 같은 transaction에서 수행
- 권한 provisioning 전 구간에서 Runtime/Writer 쓰기 차단, provisioning 후 실제 Writer 선택·재실행 성공
- Snapshot Writer UPDATE를 상태·verified_at·effective_at으로 한정; 원본 hash/identity 직접 수정 차단
- Backend Snapshot 생성도 PENDING 및 게시 시각 없음만 허용 (Worker는 이미 고정 생성)
- 예상 밖 함수 의존성이 있으면 제거·권한 회수 전체 rollback. DROP CASCADE 없음
- Source 0개 판정은 Source 범위에 한정. 다른 도메인 및 정리 도구 Trigger 제거는 후속 작업
- downgrade는 새 Trigger를 재도입하지 않고 명시적으로 거부. forward-fix/배포 전 백업 복구 정책 문서화
- 과거 migration 테스트는 398b로 고정, CI/로컬 runner는 과거 downgrade 검증 후 최신 head 적용
- CI에서도 실제 bootstrap/provisioning 테스트가 실행되도록 PostgreSQL 서비스 컨테이너 ID 전달

검증:

- 전체 Backend: 1262 passed, 2 skipped
- Source/Writer/관련 Repository 선별: 57 passed (Backend와 일부 중복)
- 과거 migration 전체: 149 passed, revision 기대값 1개 실패 후 기준 수정·별도 준비 DB 재검증 1 passed
- 확대된 최신 head 통합 시나리오: 1 passed (위 선별 테스트의 동일 테스트 재실행)
- CI/배포·환경 계약: 18 passed
- Ruff/format, 생산 코드·migration Mypy 3개, shell 구문, Trigger/RLS 재도입 검사, diff 검사 통과

기존 데이터 보존·빈 DB 전체 upgrade·Source 트리거/함수 0개·실제 분리 계정 권한·예상 밖 의존성 rollback·downgrade 거부를 폐기 DB에서 검증했다. 398b의 Source 함수 잔존 때문에 provisioning이 막히던 조건은 398c 적용 후 해소된다. 전체 #398 또는 AWS 배포 준비 완료를 의미하지 않는다. Source 관리 수정/삭제·승인/철회와 다른 도메인 제거, #404 병합 후 통합은 남아 있다. AWS 배포와 운영 DB 변경은 실행하지 않았다.

## 398d/398e 감사·처방·Candidate 함수·트리거 제거

- 398d: Runtime 전이·Check-in 감사·Evidence/Citation의 기존 Trigger 12개와 함수 3개 제거
- 398e: 저장된 처방 fingerprint와 Candidate 집계를 먼저 검증한 뒤 기존 Trigger 10개와 함수 6개, `assembly_xid` 제거
- 각 migration은 대상 테이블 잠금, 소유자 외 테이블·컬럼 권한 회수, 예상 밖 잔여 Trigger 확인을 같은 transaction에서 수행
- Runtime은 이력·불변 행에 SELECT·INSERT만 사용하며 Writer 접근은 차단
- Candidate의 불변 결과/처방 약 조회 잠금을 변경 가능한 부모 행으로 통일해 제한 역할에서도 정상 소비
- Check-in 상태 변경과 감사 append를 savepoint로 묶어 flush 실패 후 부분 저장 차단 및 재시도 확인
- 손상된 기존 처방 hash 또는 Candidate count가 있으면 정의 제거 전 migration 전체 rollback
- 최신 head의 public 애플리케이션 테이블 사용자 Trigger 0개 확인
- downgrade는 Trigger를 재도입하지 않고 명시적으로 거부

폐기 PostgreSQL 17에서 bootstrap→398b→398c→398d→398e→provisioning을 실제 분리 계정으로 검증했다. 운영 DB와 AWS 배포는 변경하지 않았다.

## 합성 정리 도구 Python 전환

- `synthetic_control.sql`의 Trigger·PL/pgSQL 함수·SECURITY DEFINER 함수 전부 제거
- 리뷰·철회 append는 Python transaction과 배치별 advisory lock으로 직렬화
- 감사 INTENT는 DB의 최신 승인자 등록·revision 순서·실행자·유효기간·철회·대상·30일 경과·현재 참조를 다시 검증해 payload 생성
- 결과 감사는 기존 INTENT payload를 계승하고 복합 FK·CHECK로 batch/object/attempt/event/reason 결속
- 정리와 모든 지원 게시 경로는 전역 advisory lock을 공유해 참조 commit과 삭제를 배타화
- 실행 계정은 감사 SELECT·INSERT만 사용하고 UPDATE·DELETE·TRUNCATE, 승인·receipt 쓰기는 거부
- 소유자는 설치·migration·장애 복구 경계로 제한
- 고정 hash 임시 허용 목록에서 합성 SQL 제거. 이후 함수·Trigger를 다시 넣으면 CI가 실패

새 `*_cleanup347_test` PostgreSQL DB에 최신 애플리케이션 migration과 control schema를 처음부터 설치해 참조·workflow 통합 57개와 cleanup 단위 103개를 통과했다. S3용 `boto3`가 없는 현재 환경에서 관련 모듈 2개를 제외한 Worker 전체는 2,569 passed, 8 skipped다.

최종 폐기 DB 카탈로그 결과는 public/source_cleanup 사용자 Trigger 0개, RLS 활성 테이블 0개, 정책 0개, source_cleanup 함수 0개다. 앞 단계에서 전체 Backend 1,263 passed, 2 skipped와 전체 migration 150 passed도 통과했다. AWS 배포와 운영 DB 적용은 수행하지 않았다. 작업 중인 #404는 코드를 선반영하지 않았으며 병합 뒤 최신 develop에서 별도 통합 검증한다.

## 최신 develop 통합 및 보호 테이블 쓰기 경계

- #393 Source Snapshot 정책, #403 Candidate API 계약을 포함한 최신 develop을 병합했다.
- #403의 확인·거절 API는 기존 Candidate Service와 부모 Search/Prescription 잠금 Repository를 그대로 사용하며 결과 테이블 직접 쓰기 경로를 추가하지 않는다.
- Source·Catalog·Prescription Version·Candidate Result·Runtime Transition·Check-in Audit·Evidence/Citation의 ORM 생성, SQLAlchemy Core DML과 문자열 raw DML을 전수 검색했다.
- `check_protected_table_writes.py`는 보호 테이블별 승인된 Python writer를 고정한다. 다른 Service·Task·Adapter에서 ORM 생성이나 직접 DML을 추가하면 CI가 실패한다.
- 이 검사는 애플리케이션 코드의 우발적 우회를 방지하는 정적 가드다. 실제 실행 계정의 DML 차단은 별도 PostgreSQL 권한 통합 테스트가 계속 담당한다.
- PR #404의 현재 head는 이 기준 develop에 아직 포함되지 않았다. 병합 뒤 새 Catalog DB adapter가 검사에 걸리면 구현과 권한을 검토한 후 승인 writer 목록을 갱신한다.
- Runtime 전이는 환경 부모 잠금, expected revision·pointer·Governance·Safety Epoch 재검증, 포인터/상태와 이력 원자 저장, revision UNIQUE 봉인까지 완료했다.

검증 결과: 최신 develop 병합 관련 Source·Candidate·Runtime·계약 408 passed, 보호 writer 정책 5 passed, Ruff/format과 보호 writer 실행 검사 통과. Trigger·RLS·PL/pgSQL 정의는 추가하지 않았고 AWS·운영 DB를 변경하지 않았다.

## 전체 migration 환경 및 최신 head 종합 검증

- Alembic migration graph가 단일 head `398f60718293`인지 코드에서 확인한다.
- CI와 로컬 전체 테스트는 과거 계약 기준 `398b` 적용 → migration 테스트 → 최신 head 적용 → 최종 카탈로그 검사 순서로 실행한다.
- 최종 카탈로그 검사는 DB revision 일치, public/source_cleanup 사용자 Trigger·RLS 활성·RLS 정책 0개, 제거 대상 함수 14개 부재를 확인한다.
- `prescription_version.assembly_xid` 제거와 Runtime 환경별 revision UNIQUE 제약도 함께 확인한다.
- 검사 실패 시 API·Worker 회귀 테스트보다 먼저 전체 테스트를 중단한다.

폐기 PostgreSQL 17 DB에서 두 경로를 독립 검증했다. 첫 DB는 빈 상태에서 `398b`까지 적용한 뒤 전체 migration 150개를 통과하고 최신 head로 전환했다. 두 번째 DB는 완전히 빈 상태에서 최신 head까지 전체 이력을 한 번에 적용했다. 두 DB 모두 최신 head 종합 검사를 통과했다. Trigger·RLS·PL/pgSQL 정의는 추가하지 않았고 AWS·운영 DB를 변경하지 않았다.

## 최신 head 전체 회귀 검증

CI와 같은 로컬 전체 runner를 깨끗한 `test` DB에서 처음부터 끝까지 실행했다. 과거 migration 계약을 확인한 뒤 최신 head와 카탈로그를 검사하고, Backend와 Worker를 분리된 실행 환경에서 병렬 검증했다.

- migration: 150 passed
- Backend·Contract·PostgreSQL: 1,620 passed, 59 skipped
- Redis 통합: 23 passed
- Worker: 2,620 passed, 8 skipped
- 통합 coverage: 93%
- 실제 분리 Runtime·Writer 계정 및 배포 provisioning 선별 재검증: 5 passed
- 전용 `source_cleanup347_test` DB 통합 검증: 57 passed

Source cleanup 검증 후 최신 head 종합 검사를 다시 실행해 public/source_cleanup 사용자 Trigger 0개, RLS 활성·정책 0개, 제거 대상 함수 0개를 확인했다. skip은 외부 provider가 필요한 smoke와 별도 DB에서 수행하는 Source cleanup 범위이며, Source cleanup은 위 전용 실행에서 모두 통과했다. AWS 배포와 운영 DB는 변경하지 않았다.
