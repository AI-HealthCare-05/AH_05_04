# PD-398: Python Snapshot 상태 전이

상태: 작업 브랜치 부분 구현, 리뷰·권한 구성·migration 대기. 현재 배포 계약으로 승격하지 않는다.

구현: 김지혜. 검토: 송은영(DB·권한), 정현우(Source), 권가빈(제품 수용).

## DB 함수 대체

`SqlAlchemySourceSnapshotRepository.change_snapshot_status()`는 DB 상태 전이 함수를 호출하지 않는다. 같은 transaction에서 Operation을 먼저 잠그고 Snapshot을 잠근 뒤 현재 상태를 재확인한다. 대상 부재나 expected status 불일치는 false이며 변경하지 않는다.

| 전이 | verified_at 입력 | effective_at 입력 |
| --- | --- | --- |
| PENDING → CURRENT | 필수 | 필수 |
| PENDING → FAILED | 필수 | 금지 |
| CURRENT → STALE | 금지 | 금지 |
| STALE → CURRENT | 금지 | 필수 |

나머지 전이는 거부한다. 입력 시각은 timezone-aware이어야 한다. 입력하지 않은 기존 verified_at/effective_at은 보존한다.

CURRENT 선택은 공백이 아닌 100자 이하 작업자 식별자가 필요하다. 서비스에서도 기존 CURRENT를 STALE로 바꾸기 전에 이 조건을 검사한다. 이는 기존 optional 인자의 허용 범위를 강화한 변경이며, 호출자는 서버에서 확인한 작업자 식별자를 전달해야 한다. 문자열의 존재 자체가 인증·권한을 의미하지는 않는다.

거부 레코드가 있는 Snapshot을 CURRENT로 선택하려면 `snapshot-publication-approval` PASSED와 공백이 아닌 검토자 기록이 필요하다. 검사 후 조건부 UPDATE와 `snapshot-current-selection` 감사 INSERT를 같은 savepoint에 묶는다. 감사 실패를 호출자가 잡더라도 상태 변경만 남지 않는다. 외부 transaction의 commit/rollback은 호출자가 담당한다.

여러 Snapshot 교체와 실패 검증 기록 등 상위 업무 전체의 원자성은 외부 transaction owner가 보장한다. 도중 오류를 삼키고 부분 작업을 commit하지 않는다.

## 배포 전 필수 조건

- Source Writer의 실제 credential과 실행 프로세스 분리
- Runtime의 보호 테이블 쓰기 및 Writer 역할 획득 차단
- 선택·승인·철회 등 모든 쓰기 경로의 공통 Operation 잠금
- 기존 Trigger·전용 함수 및 EXECUTE 권한 제거 migration
- 초기화 스크립트·default privileges 정렬
- 실제 제한 역할을 사용하는 통합 검증

현재 변경만으로 기존 Runtime 배포에 적용하지 않는다. Runtime에 UPDATE 권한을 임시 부여하거나 관리자 credential을 주입하는 방식으로 연결하지 않는다. Trigger/RLS를 새로 만들어 전환을 우회하지 않는다.

## 증빙

### Source 역할 적용 코드 (배포 미연결)

`infra/python/source_role_policy.py`는 관리자 연결의 외부 transaction에서 호출한다. 비밀번호를 받거나 역할을 생성하지 않으며, 사전에 생성된 별도 Runtime/Writer 역할에 권한을 적용한다.

- Source 7개 테이블에서 Runtime은 SELECT만 허용
- Writer는 SELECT·INSERT 허용. Operation 잠금, Snapshot 전이, Ingestion Run 갱신에만 UPDATE 허용
- Artifact·Verification의 UPDATE·DELETE·TRUNCATE는 Writer에도 금지
- Runtime/Writer의 관리자 권한, 역할 상속, schema·database·객체 소유권이 있으면 적용 거부
- 기존 상태 전이 DB 함수가 남아 있으면 권한 전환 거부
- PUBLIC 및 Runtime/Writer의 테이블 default privilege 정리
- 새로운 테이블의 권한은 자동으로 열지 않고 해당 migration에서 명시

Source 관리용 수정·삭제 권한과 Catalog/Prescription/Runtime 권한은 이 Source Writer에 자동으로 포함하지 않는다. 각 도메인 구현과 함께 명시적으로 추가한다.

현재 `configure-app-role.sql`의 기존 광범위 권한 부여 및 배포 호출 경로는 아직 전환하지 않았다. 새 정책 적용 후 기존 스크립트를 실행하면 권한이 다시 열릴 수 있으므로, 이 모듈을 운영에 단독 적용하지 않는다. 제거 migration·초기화 스크립트·별도 Writer 실행 구성을 같은 배포에서 연결해야 한다.

`test_source_writer_roles.py`는 서로 다른 로그인 자격 증명으로 실제 INSERT/UPDATE/DELETE/TRUNCATE 및 SET ROLE 차단, Writer 허용 작업, 신규 테이블 기본 권한, 역할 상속 거부를 확인한다.

`tests/integration/rag/test_source_snapshot_lifecycle.py`는 모델 스키마만 생성하고 과거 전이 함수·Trigger 설치 없이 Python 저장 경로를 검증한다. 최종 migration과 실제 역할 분리 검증은 별도로 남아 있다.

### 일회성 Source Writer 실행 경로

`python -m ai_worker.admin.source_writer <snapshot UUID> --expected-checksum <SHA-256> --reason-code <고정 코드>`로 검증된 Snapshot을 선택한다. `SOURCE_WRITER_HOST/PORT/NAME/USER/PASSWORD/ACTOR`를 모두 별도로 주입한다. 기존 Runtime·Migration·Admin 비밀번호 환경 변수가 함께 있으면 실행을 거부한다. ACTOR는 운영자가 확인한 작업자 식별자이며 사용자 요청에서 그대로 받지 않는다. 실행 권한과 secret 배포 권한은 운영자에게만 부여해야 한다.

Operation→Snapshot 잠금 후 checksum을 대조하고 기존 Python 선택 서비스를 호출한다. 상태 변경·선택 감사·사유 코드 감사는 실행 명령이 소유한 하나의 transaction에서 commit한다. 실패하면 전체 rollback하며 오류 원문이나 SQL parameter는 출력하지 않는다. 이미 CURRENT인 대상의 재선택은 감사 행을 추가하지 않는다. 사유는 원문 대신 100자 이하 대문자·숫자·밑줄 코드로 제한한다.

운영 Compose의 `source-writer`는 `source-admin` profile의 일회성 서비스이며 기본 배포에 포함되지 않는다. 기존 AI 이미지의 별도 진입점을 사용하고 Writer 환경 변수만 전달한다. 예시: `docker compose -f docker-compose.prod.yml run --rm --no-deps source-writer <UUID> --expected-checksum <SHA-256> --reason-code VERIFIED_RELEASE`.

이 실행 경로 추가는 운영 전환 완료가 아니다. 전용 역할 provisioning, 기존 함수·Trigger 제거, bootstrap 권한 정렬이 완료되기 전에는 운영 실행하지 않는다. 통합 테스트는 실제 제한 역할로 명령의 DB 실행 함수(run_selection)를 호출하여 성공·재실행·Runtime 차단·감사 권한 실패 rollback을 확인한다. 컨테이너 실행 및 운영 provisioning을 포함하는 배포 통합 검증은 후속 단계에 남아 있다.


실행 시 실제 로그인 역할의 관리자 권한·다른 역할 membership·DB/schema/객체 소유권을 다시 검사하여 잘못 주입된 고권한 계정을 거부한다. Source 역할 정책은 Migration owner가 PUBLIC 또는 Runtime/Writer에 부여한 전역 테이블 default grant가 있으면 적용을 거부한다. Schema 범위의 REVOKE로 전역 grant를 취소할 수 없기 때문이다. 다른 schema에 영향을 주는 전역 권한을 자동 변경하지 않으며, 운영 provisioning에서 먼저 정렬해야 한다.

### 검증 이력과 전환의 잠금 일치

Worker의 append_verification 및 Backend Source/Catalog Repository의 create_snapshot_verification은 INSERT 전 해당 Snapshot의 Operation 행을 잠근다. 상태 전환과 승인 이력이 서로 다른 잠금 경로로 경합하지 않도록 한다. 대상이 없으면 기록하지 않는다. 이는 작업자의 승인 권한을 대신하지 않으며 관리 승인·회수 API와 배포 권한 연결은 별도로 남아 있다.
