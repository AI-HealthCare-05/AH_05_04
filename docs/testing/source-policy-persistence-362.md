# #362 Source 정책 영속화 및 #165 잔여 완료 조건

기준: #429 병합 develop `0eedc8d`. 구현 김지혜, Source/DB 책임 리뷰 송은영,
Source·Catalog 계약 리뷰 정현우. 승인된 PD-362-20260909를 구현한다.

## 단계별 완료 기준

| 범위 | 구현 상태 | 종료 증빙 |
| --- | --- | --- |
| Source 정책 DB 저장·소비 | 1단계 구현 | Source별 정책 왕복, DB 정책보다 느슨한 요청 차단 |
| Snapshot external_version 보존 | 1단계 구현 | 새 Snapshot exact roundtrip, 기존 값 추정·소급 수정 없음 |
| Snapshot·Citation version 200자 | 1단계 구현 | forward migration, 201자 기존 데이터 무변경 중단, FK 보존 |
| Run 시도 provenance | 2단계 구현 | CREATED/NO_CHANGE/충돌/invalid version별 DB Receipt |
| Snapshot Receipt·Writer 선택 검증 | 3단계 구현 | DB identity/version/hash/승인 참조 조회, 외부 version 변조 선택 차단 |
| Catalog·Runtime 실제 소비 연결 | 인계 대기 | #166/#372 및 Runtime 담당 작업에서 승인·Freshness와 함께 소비 |
| #165 reject_code allowlist·version | 계약 미확정 | 정현우 Source 계약 확인 필요, 임의 목록 생성 금지 |
| #165 Source→#166 Catalog 인계 | 미완료 | #166 실제 소비 통합 검증까지 추적 |
| #178 Freshness 계산 | 별도 담당 범위 | #362에서 계산 구현·완료를 선언하지 않음 |

#362/#165를 이 문서만으로 닫지 않는다. #166에서 확인할 소비 조건은 인계 후 완료 증빙을 연결한다.

## 1단계 저장·검증 경계

- 기존 `rag_source`에 `max_rejected_records`, `max_rejection_rate`, `empty_result_policy`를 저장한다.
- 기본은 0건·0비율·REJECT다. 현재 구현하지 않은 빈 결과 정책은 허용하지 않는다.
- 수집 실행의 요청 정책과 DB 정책을 모두 검사한다. 요청값으로 DB의 거부 한도를 완화할 수 없다.
- Source 정책 초기 생성은 기존 Source Repository에서 Python 검증 후 저장한다.
  관리 API에 새로운 정책 수정 필드를 임의 추가하지 않는다.
- 거부가 한도 이하라도 Snapshot은 PENDING이며 publication 승인 경계를 유지한다.
  `manual_review_required_on_rejection=false` 같은 승인 우회 설정을 도입하지 않는다.
- 외부 버전은 새 Snapshot에 입력값 그대로 저장한다. 과거 Snapshot의 외부 버전을 추정하지 않는다.
- migration은 기존 Snapshot·Citation 중 200자 초과 값이 있으면 원문을 출력하거나 자르지 않고 중단한다.
- downgrade는 데이터 손실이 없는 조건에서 허용한다. `362a`는 Source 정책이 기본값이고
  Snapshot external_version이 모두 NULL이어야 하며, `362b`는 새 시도 provenance 컬럼이
  모두 NULL이어야 한다. 검사는 쓰기 잠금 이후 컬럼 제거 전에 수행한다.
- Python Service/Repository가 업무 판정과 transaction을 담당한다. 일반 CHECK/FK와 최소 권한은 보조 방어다.
- 신규 DB 함수·Trigger·RLS 정의 및 재도입 검사 예외 추가는 없다.

## 검증 환경

작업 전용 PostgreSQL 컨테이너의 `issue362_validation` DB, 해당 DB 소유 일반 계정.
AWS·팀 개발 DB·운영 DB는 적용하지 않는다.

역할 생성이 필요한 기존 Source Writer 테스트 2개는 일반 계정의 CREATEROLE 부재로 실행이 차단되었다.
역할 생성 권한을 새로 부여하지 않았으며, 나머지 기능·migration 검증과 구분해 기록한다.

## 2단계 검증

- Source 단위 테스트 366 passed.
- 실제 DB Source lifecycle·Receipt·NO_CHANGE 관측 충돌·invalid 원문 비저장·rollback: 24 passed, 역할 생성 검사 2개 별도.
- 전체 Backend/Worker mypy: 535개 파일 통과.
- 기존 role policy는 Source Run lifecycle 컬럼만 UPDATE하도록 축소한다.
- 신규 migration은 기존 비소유자의 테이블 단위 Run UPDATE 권한도 같은 컬럼 범위로 축소한다.
- 기존 Run의 미관측 시도 version을 Snapshot에서 추정·소급 채우지 않는다.

## 3단계 및 최종 검증

- Snapshot Receipt는 Source/Endpoint/Operation/Snapshot ID, Source code, version/external version,
  canonical checksum·canonicalization version·Endpoint Receipt hash, 상태·거부 건수와 검증 seal을 반환한다.
- 사람의 publication 승인 Verification ID는 seal과 별도로 반환한다. seal 자체는 publication 승인이 아니다.
- Source Writer는 CURRENT 선택 전에 저장된 version·external version·checksum·Receipt 결속을 검증한다.
- 과거 외부 version이 누락된 Snapshot을 추정 승인하지 않는다. 사용 전에 별도 검토·정상 재수집이 필요하다.
- Catalog 실제 DB 연결은 #166/#372, Runtime 소비·Freshness 계산은 해당 담당 범위다.
  이 Receipt만으로 Source 활성화·Freshness 적합·Runtime 공개를 선언하지 않는다.

검증 결과:

| 검사 | 결과 |
| --- | --- |
| 전체 Worker 단위 테스트 | 2,714 passed, 8 skipped |
| Source lifecycle 및 migration 데이터 보존 | 27 passed, 2 deselected (역할 생성 권한 필요) |
| publication 참조 추가 후 Receipt 재검증 | 2 passed |
| DB 역할·관리 배포·Source Governance·재도입·쓰기 경계 계약 | 28 passed |
| 전체 Backend/Worker mypy | 535개 파일 통과 |
| Ruff / format / diff | 통과 |
| 실제 Alembic head | `362b2c3d4e5f` 단일 head |
| 최종 DB 사용자 정의 Trigger / RLS / 제거 대상 함수 | 0개 |

`run_test.sh` 전체 CI와 역할 생성이 필요한 검사를 전부 통과한 것으로 보고하지 않는다.
전용 DB의 일반 계정에서는 CREATEROLE·REPLICATION 권한 테스트를 실행할 수 없으며,
새 워크트리에서 `run_test.sh`는 정적 gate 통과 후 `envs/.local.env` 부재로 중단됐다. 새 SUPERUSER 계정은 생성하지 않았다.

#362 자체 종료 전 담당 리뷰, 전체 CI와 소비자 인계를 확인해야 한다. #165의 allowlist 미확정과
normalization 실행 참조의 미확정 경계는 그대로 유지한다. #166의 v2 hash나 실행 ID를 임의로 대체하지 않는다.

## 푸시 전 #362 이슈 본문 대조

2026-09-10 GitHub #362 본문과 댓글을 재조회했다. 댓글은 없으며 본문은 최초 요구사항을
유지하고 있다. 구현 판정은 본문과 함께 승인된 `PD-362-20260909` 및 #361의 후속 순서를
대조한다. 아래 완료는 해당 코드·문서의 구현 상태이며 이슈 전체 Close나 승인 상태가 아니다.

| 이슈 요구 | 대조 결과 | 근거 |
| --- | --- | --- |
| source_version 문법·생산자 검증 | 구현됨 — 선행 병합분 + 이번 저장 연결 | `source_version.py`, `persistence.py`, `failure_runs.py`; invalid 원문 대신 hash·길이·reason 저장 |
| 외부 Version 보존 위치 | 구현됨 | Snapshot `external_version`, Run `attempted_external_version`; 새 version의 NO_CHANGE 관측은 Run에 보존 |
| 200/255 길이 통일 | 구현됨 | Snapshot·Citation 모델 및 `362a1b2c3d4e`; 기존 201자 데이터는 무변경 중단 |
| #178 선행을 #361 적용 순서에 반영 | 선행 문서에 반영됨 | #361 본문 및 PD-315의 후속 순서에 #362 Source 생산 경계 → #178 명시 |
| CURRENT/STALE와 파생 Freshness 정합성 | 의미 분리 문서화됨 | PD-362: 저장 상태는 선택·승인 이력, Freshness는 승인 Policy와 평가 시각의 파생 결과; #178 계산은 제외 |
| Source별 거부 정책 컬럼 | 구현됨 | Source 모델·생성 입력·migration·DB 정책 조회; 요청 정책으로 DB 정책 완화 불가 |
| 임계치·자동 승인 경계 | 승인 Decision 기준 구현됨 | Hard Limit 초과는 FAILED·Snapshot 없음, 한도 내 거부는 PENDING·별도 publication 승인 필요 |
| empty_result_policy | REJECT만 구현됨 | Source 기본값·DB CHECK·Python 정책 판정; 빈 결과는 실패 |
| migration 단일 head | 구현·전용 DB 검증됨 | `3984b5c6d7e8 → 362a1b2c3d4e → 362b2c3d4e5f` |
| downgrade 왕복 | 조건부 구현 | 기본 정책·미기록 provenance에서 왕복 허용; 손실 가능 데이터가 있으면 schema 변경 전에 중단 |
| 회귀 테스트와 CI | 관련 검사는 통과, 전체 CI는 미완료 | 아래 재검증 및 앞 절의 환경 제한 참조 |

### 초기 본문과 구분할 사항

- 본문의 “기본값은 기존 동작과 동일”은 PD-362가 거부 0건·0비율·REJECT 기본값으로
  명시적으로 대체했다. 기존의 묵시적 거부 허용을 복원하지 않는다.
- 본문의 “임계치 초과 시 수동 검수 대기”는 PD-362에서 **한도 초과 시 실행 실패**,
  **한도 내 거부 시 PENDING 후보**로 구분됐다.
- `manual_review_required_on_rejection`을 가변 정책 컬럼으로 만들지 않았다.
  거부 레코드가 있으면 publication 승인이 필요하다는 Python 규칙을 유지한다.
- 최초 구현의 일괄 downgrade 차단은 재검토 후 조건부 downgrade로 수정했다.
  기존 정책·외부 Version·시도 이력을 삭제하지 않고 되돌릴 수 있는 경우만 허용한다.
- Snapshot Receipt 제공·Source Writer 검증까지 구현했으나 Catalog/Runtime 실제 소비
  연결은 #166/#372 및 해당 Runtime 담당 작업에 남아 있다. #362 전체 완료를 선언하지 않는다.

### 이번 재검증

- Source ingestion 단위 테스트: **366 passed**.
- Source lifecycle·DB 왕복·migration 보존 검사: **27 passed, 2 deselected**.
  제외한 두 건은 앞서 기록한 역할 생성 권한 검사이며 새 권한을 부여하지 않았다.
- Ruff 검사와 format: 통과, **682 files already formatted**.
- DB 함수·프로시저·Trigger·RLS 재도입 검사와 보호 테이블 Python 쓰기 경계 검사: 통과.
- 전체 변경 diff의 공백 오류 검사: 통과.
- 최신 `origin/develop`은 `c97f20f`이며 기반 `0eedc8d` 이후 추가 변경은 #427 Frontend와
  관련 테스트 문서다. 이번 변경 파일 및 migration과 겹치지 않는다.

GitHub 이슈 체크박스·본문은 수정하지 않았다. 리뷰 시 이 대조표와 승인 Decision을 함께
사용하고, #165 reject_code 초안은 이번 #362 브랜치에 포함하지 않는다.

## downgrade 재검토 및 수정

#362 전체를 forward-fix 전용으로 제한하지 않는다. 아직 병합되지 않은 두 migration의
downgrade를 수정했으며 upgrade와 revision 연결은 변경하지 않았다.

| 대상 | downgrade 허용 조건 | 처리 |
| --- | --- | --- |
| `362b2c3d4e5f` → `362a1b2c3d4e` | 모든 Run의 새 provenance 6개 컬럼이 NULL | 해당 컬럼·CHECK·인덱스 제거, 기존 Run 보존 |
| `362a1b2c3d4e` → `3984b5c6d7e8` | 모든 Source가 0건·0비율·REJECT이고 모든 Snapshot external_version이 NULL | 정책·외부 Version 컬럼 제거, Snapshot/Citation 길이 255 복원, 복합 FK 유지 |

- 사용 중인 정책이나 외부 Version·시도 기록이 하나라도 있으면 상수 오류로 중단한다.
  빈 JSON이나 0 byte 길이의 감사 기록도 기록된 데이터이므로 삭제하지 않는다.
- 쓰기 잠금을 먼저 획득하여 검사 이후 데이터가 추가되는 경합을 막는다.
- PostgreSQL의 기존 Alembic transaction 안에서 실행한다. 여러 revision을 함께 되돌리다
  후속 검사에서 실패하면 앞 revision의 DDL도 rollback한다.
- downgrade 후에도 Run의 제한된 UPDATE 권한을 유지한다. 기존의 광범위한 테이블 권한이나
  PUBLIC 권한을 복원하지 않는다. 따라서 schema 왕복이 과거 ACL 전체 복원을 뜻하지 않는다.
- 기본 정책의 제거는 이전 Python의 동일 기본 정책으로 돌아가는 경우다. downgrade 후에는
  해당 schema와 호환되는 이전 애플리케이션을 사용해야 한다.
- 되돌릴 수 있는 하한은 이번 두 revision의 부모 `3984b5c6d7e8`이다. #398의 Trigger 제거·seal
  보호 등 기존 downgrade 금지 구간을 변경하거나 넘어서지 않는다. 새 Trigger·RLS·업무 DB 함수는 없다.
- 모든 migration을 forward-fix로 운영한다는 팀 정책을 새로 도입하지 않는다.

검증은 테스트 전용 schema의 합성 데이터에서 migration 코드를 실제 PostgreSQL에 실행한다.
기본 정책/기존 Run의 upgrade → downgrade → upgrade, Citation FK 보존, 각 데이터 손실 조건의
무변경 중단, 중간 revision rollback, PUBLIC UPDATE 비복원을 포함한다.

수정 후 검증 결과:

- Source lifecycle + migration: **37 passed, 2 deselected** (기존 역할 생성 권한 검사).
- Ruff / format: 통과. mypy: **535개 파일 통과**.
- Trigger·RLS 등 재도입 검사, Python 쓰기 경계 및 테스트 분류 검사: 통과.
- 전체 CI 재시도는 정적 검사 후 기존과 동일하게 `envs/.local.env` 부재로 중단했다.
  전체 migration suite나 역할 생성 테스트를 통과했다고 표시하지 않는다.

## PR #436 CI 회귀 수정

원격 CI의 `test-migration`, `test-backend` 실패와 이를 집계한 `test` 실패를 확인했다.
원인은 아래 테스트 fixture와 최신 schema 사이의 불일치였다.

- 최신 head 검사 두 곳이 과거 `3984b5c6d7e8`을 상수로 사용했다. 저장소에서 단일 head를
  구하고 실제 DB 및 Docker 이미지의 head와 비교하도록 변경했다.
- 역할 provisioning 테스트의 축약 Run 테이블에는 `id`만 있었다. 컬럼별 UPDATE 정책을
  검증할 lifecycle 컬럼과 변경 금지 대상 provenance 컬럼을 fixture에 추가했다.
  Writer의 lifecycle UPDATE 허용·provenance UPDATE 거부와 Runtime UPDATE 거부도 검사한다.
- 과거 revision 데이터 보존 테스트에서 최신 Source ORM이 아직 없는 정책 컬럼을 INSERT했다.
  역사적 스키마를 검증하는 입력은 해당 시점의 컬럼으로 넣고, 최신 Service 동작 검증과 분리했다.
- Snapshot lock-marker migration의 기존 hash 보존은 그 revision에서 검사하고,
  최신 head 적용 후에는 과거 컬럼 값들이 그대로 보존됐는지 별도로 검사한다.
  외부 Alembic 프로세스의 DDL 이후에는 테스트 연결의 오래된 prepared statement를 폐기한다.
- Trigger 제거 후 최신 Writer까지 실행하는 합성 fixture에는 승인된 API Version 문법과
  checksum·Endpoint Receipt 결속을 넣었다. 운영 데이터를 소급 보정하지 않는다.

이번 수정은 테스트와 검증 문서에 한정한다. 애플리케이션 권한을 확대하거나,
검사를 skip 처리하거나, Trigger·RLS·업무 DB 함수를 추가하지 않는다.
과거 migration 자체도 변경하지 않는다.

검증 환경은 기존 작업 전용 `issue398-postgres` 컨테이너의 신규 `issue436_ci_validation` DB와
테스트가 생성·정리하는 격리 DB/schema다. 기존 컨테이너 관리 연결을 사용하며 새 관리 계정이나
운영 권한을 부여하지 않는다. Docker 이미지 내 Runtime import와 head 일치 검사 2건,
관리·Writer·bootstrap 관련 25건 및 수정 후 과거 행 보존 3건을 통과했다.

추가 검증:

- 전체 migration suite: **170 passed**.
- 최신 head upgrade 및 최종 DB 상태 검사: **362b2c3d4e5f**, Trigger/RLS/제거 함수 **0개**.
- Source lifecycle 전체: **27 passed**, 앞서 권한 부족으로 제외했던 Writer 검사 2건도 통과.
- Ruff / format / diff 검사와 mypy 535개 파일, 재도입·쓰기 경계·테스트 분류 검사 통과.
- 이전 절의 전체 migration·Writer 권한 검사 미완료 기록은 이 재검증으로 해소했다.
  전체 Backend 및 집계 CI의 최종 상태는 수정 커밋의 GitHub Actions 결과로 별도 확인한다.


## PR #436 리뷰 반영 (2026-09-11)

- 정현우 리뷰의 MUST FIX 2건을 Python 계층에서 수정했다.
- 송은영은 DB·migration·transaction 범위 이상 없음을 확인했으며, 긴 Version의 감사 진입 누락 지적에 동의했다.
  이를 전체 변경 승인으로 해석하지 않는다.
- 길이 검증을 input metadata 생성 시점에서 기존 저장 orchestration의 검증·실패 기록 경계로 이동했다.
  원본 Artifact 보관과 Snapshot 생성 전에 검증하며 하위 저장 경로와 DB 제약도 유지한다.
- 201·202·300자, 한글 201자, 제어문자를 실제 저장 orchestration에 전달하여 FAILED Run과
  hash·UTF-8 byte length·사유 보존, Snapshot/Artifact 생성 없음, 원문 저장·repr·로그 미노출을 검증한다.
- Attempt Receipt는 직렬화 가능한 `decision`을 직접 제공한다. 네 판정의 DB 왕복과
  성공(거부 레코드 포함)·실패·미지정 코드·불일치 상태에 대한 단일 fail-closed 매핑을 검증한다.
- 새 migration·Trigger·RLS·업무 DB 함수·권한 확대 없음. 기존 downgrade 및 미확정 후속 범위 유지.

리뷰 수정 검증: Source ingestion 단위 384 passed, 실제 DB lifecycle·Receipt 31 passed, mypy 536개 파일 및 Ruff·format·재도입 방지·쓰기 경계·테스트 분류 검사 통과. 원격 CI 결과는 해당 커밋의 실행 결과로 별도 확인한다.


### 최신 develop / #416 충돌 해결

기반 develop `086b2aa`(#416 포함)를 병합했다. Source 계약 설명과 Runtime 계약 설명을 함께 보존하고,
공통 migration·Docker 검사는 단일 코드 head와 실제 DB/이미지를 비교한다.
`362c3d4e5f60`은 `(362b2c3d4e5f, 175a1b2c3d4e)`를 잇는 DDL 없는 merge revision이다.
기존 #362 및 이미 병합된 #416 migration 파일은 수정하지 않았다.
두 기존 head에서 새 head로 upgrade할 때 기존 Source 행을 보존하고 Trigger·RLS·제거 대상 함수가
없는지 검사한다. downgrade에서 merge revision 자체는 부모 두 경로만 복원하며,
각 부모 migration의 데이터 보존 guard는 계속 적용된다.

#416의 downgrade 검사는 최신 head에 대한 상대 `-1` 대신 해당 `175a1b2c3d4e`에서
부모 `3984b5c6d7e8`까지 명시하여 검사한다. 부모 revision 자체의 downgrade는 실행하지 않는다.
행이 있으면 거부, 비어 있으면 identity 컬럼 제거 후 복원이 되는 기존 검증 의미를 유지한다.

충돌 해결 후 Source·Runtime·관리·양쪽 head 병합 연관 검사: **482 passed**.
전체 CI runner는 `envs/.local.env`가 없어 환경 준비 단계에서 중단했다. 직접 실행한 검사와 원격 CI를 구분한다.

최종 검증: 전체 migration **177 passed**, Runtime Repository·Docker 이미지·head·Writer/역할 권한 **40 passed**.
작업 전용 DB의 실제 head upgrade 및 종합 검사도 **362c3d4e5f60; Trigger/RLS/제거 함수 0개**로 통과했다.
Ruff·format, mypy 540개 파일, 재도입 방지·보호 테이블 쓰기 경계·테스트 분류·diff 검사를 통과했다.
