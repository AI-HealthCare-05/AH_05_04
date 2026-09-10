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
- downgrade는 provenance 손실을 막기 위해 중단하고 forward-fix를 요구한다.
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
