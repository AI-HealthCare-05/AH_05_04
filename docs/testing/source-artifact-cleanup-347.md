# #347 읽기 전용 조사·배치 사전 검사 검증

- 구현: 김지혜. DB·보안 검토: 송은영, Source·provenance 검토: 정현우, 정책·배치 승인: 권가빈.
- 기준: `8da32d7` 및 이 문서와 함께 커밋하는 2단계 변경. develop 기준은 `a6e5645`.
- 정책: [#335 보존·삭제 정책](../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md)
- 계획: [1단계 경계 조사](../designs/jye-rookie/issue-347-source-artifact-cleanup-plan.md)
- 현재 상태: 2·3단계 내부 조사·사전 검사와 4단계 임시 Local 합성 파일 삭제·파일 감사·복구 모델 구현. 실제 Source adapter·승인 저장소·공유 writer 잠금·운영 감사 DB 연결은 미구현.
- 아래 2단계 결과는 해당 시점 증빙이며, 최신 3단계 결과는 마지막 절에 기록한다.

## 구현된 경로

`survey_candidates`는 inventory 및 reference 포트만 사용한다. 삭제 포트·scheduler·Runtime 설정은
없으며 public API나 DB schema를 추가하지 않는다. enum과 사유 문자열은 내부 조사용이며
기존 Source 상태·오류 코드 또는 운영 승인을 대체하지 않는다.

- Local reader: 절대 root의 모든 경로 요소를 descriptor-relative `O_NOFOLLOW`로 연다.
  현재 사용자 소유의 0700 root만 조사하고 내용 주소 구조의 정규 파일을 읽는다.
  실제 SHA-256·크기 및 읽기 전후 파일 identity/metadata 변화를 확인한다. root를 만들거나 chmod하지 않는다.
  symlink·임시 파일·예상 밖 항목·내용 불일치는 전체 inventory를 불완전 처리한다.
  파일의 atime은 OS 읽기 정책에 따라 바뀔 수 있지만 파일 내용·DB 행은 변경하지 않는다.
- SQL reader: 동일 backend/key 전체 행 count만 SELECT한다. Run 상태/최신 Snapshot 조건이나
  join을 사용하지 않아 FAILED·NO_CHANGE·여러 Run의 참조를 일부 제외하지 않는다.
  commit·delete·update는 호출하지 않는다. 쿼리 오류는 조사 서비스가 고정 사유로 보류한다.
- 판정: 직접/간접 참조 양수는 보호한다. DB·namespace 불일치, 불완전 조회, 수집 상태 불명,
  Source 소유 불명, 생성 시각 불명·미래·timezone 누락, 30일 이하 보존기간은 보류한다.
- 검토 후보: 모든 사실이 확인된 합성 입력에서만 `REVIEW_CANDIDATE`를 반환한다.
  실제 삭제 가능 상태나 배치 승인이라는 의미는 없다. `source-artifact-retention-v1` 문자열 일치는
  지원 정책 선택일 뿐 PM 승인 검증이 아니다.
- 보고서: 원문 bytes·DB id·root·object key·예외 원문 없이 안전한 hash 참조와 고정 사유를 반환한다.
  object_ref는 namespace/key에 대한 상관관계 식별자이며 승인 목록 hash나 객체 세대 식별자가 아니다.
  어떤 객체라도 보류되면 result.complete=false다. 정렬은 object key 순서로 결정적이다.

## 중요한 미완료 경계

Local 객체의 mtime/ctime이나 DB row created_at을 생성 시각으로 대체하지 않는다.
Local reader는 created_at=None, source_owned=false를 반환한다. SQL reader는 count=0이어도
namespace·전체 외부 참조·진행 중 수집을 증명하지 않으므로 scope_complete=false를 유지한다.
따라서 실제 adapter만 연결해서 무참조 운영 파일을 후보 확정할 수 없다.

합성 테스트 포트는 완전한 증거를 제공하는 상황을 모델링할 뿐이다. 사용자 입력 boolean을
운영 승인으로 받는 API/CLI는 없다. 1단계 Q1/Q2의 실제 증거 제공 방식과 Q3 경합 보호를
구현·검토하기 전에는 이 내부 모델을 삭제 경로에 직접 연결하지 않는다.

DB id는 조사 조립부의 식별자이며 서버 실체·저장소 귀속의 검증 결과가 아니다.
디렉터리 조사 역시 동시 쓰기 전체를 잠그는 snapshot이 아니며 삭제 직전 검사를 대체하지 않는다.
외부 증빙/신규 참조 테이블 감지·승인 검증·공유 lock·감사 저장·운영 S3 실행은 이번 범위 밖이다.

## 실행 결과

```text
PYTHONPATH=. python -m pytest ai_worker/tests/rag/source_cleanup -q
36 passed

PYTHONPATH=. python -m pytest ai_worker/tests/rag -q
878 passed

ruff check .
All checks passed!
ruff format . --check
532 files already formatted
MYPYPATH=backend:. python -m mypy backend/app ai_worker
Success: no issues found in 450 source files

git diff --check
통과
```

Python 3.13 로컬 환경, Local 파일은 pytest tmp_path의 합성 파일만 사용했다.
SQL 참조 조회는 AsyncSession mock과 생성 SELECT·parameter 검증이다. 실제 PostgreSQL에서
FAILED/NO_CHANGE/간접 참조를 생성한 통합 검증이나 실 DB 권한 검증은 아직 수행하지 않았다.
878건에는 신규 36건이 포함된다. 전체 Backend·Worker CI 또는 T01–T30 완료를 의미하지 않는다.

## 정책 시나리오 대응

| 영역 | 이번 증빙 | 남은 검증 |
| --- | --- | --- |
| T01/T07/T08 | 잘못된 정책·기준 시각, 불완전 inventory, 참조 실패, 30일 경계·소유 불명 보류 | 정책 승인 증거 연결 |
| T03–T06/T30 | 전체 직접 SELECT, 공유 count 보호, 간접 참조 증거 양수 보호, 범위 불명 보류 | 실제 DB 상태별·downstream·schema inventory 통합 |
| T23/T24/T28/T29 | 다른 DB/root 보류, Local symlink·예상 밖 파일·변조 bytes 차단 | 승인 후 변경/최종 삭제 직전 재검사 |
| T02/T09–T22/T25–T27 | 삭제·scheduler 실행 경로 없음 | 승인·경합·삭제·감사·복구·운영 인계는 3~5단계 |


## 3단계 — 고정 배치·승인 결속 및 보호 경계 사전 검사

기준은 2단계 `ea12a81`과 이 문서에 동반되는 3단계 변경이다. develop 기준은 여전히
`a6e5645`이며 최신 원격 develop 동기화나 CI 통과를 주장하는 증빙은 아니다.

`preflight.check_batch`는 삭제 없는 내부 dry-run 함수다. Public API/CLI, Source writer,
DB schema, Runtime 설정은 변경하지 않는다. 아래 포트·자료형은 검토용 내부 구현이며,
실제 승인 저장 형식이나 공유 잠금 방식을 확정하지 않는다.

- 고정 배치 hash: 정책 version, DB, namespace, backend, 합성 환경, 정렬한 대상 목록을
  결속한다. 각 대상에는 object key·실제 bytes checksum·크기·생성 시각·세대 식별자가 포함된다.
  목록 순서만 달라지면 같은 hash이고, namespace나 객체 세대 등이 바뀌면 다른 hash다.
- 승인 verifier: 신뢰할 수 있는 구현이 승인자의 신원·권한·철회 상태를 검증해야 한다.
  반환 증거의 배치 hash·정책·PM/DB 검토자·실행자·유효기간을 대조하고, 느린 재조회 후
  다시 검증하여 대기 중 만료·철회를 거부한다. 합성 verifier만 테스트에 존재한다.
- guard 포트: 모든 객체 쓰기·재사용·참조 생성을 배제하는 구현을 요구한다. 경계 안에서
  실제 관측 결과와 배치를 대조하고 전체 참조·수집 상태·보존기간을 다시 검사한다.
  guard 상실·관측 오류·경계 해제 오류는 실패이며, 예외를 삼키는 guard도 성공으로 보고하지 않는다.
- 포트가 없거나 증명이 불완전하면 통과하지 않는다. `SYNTHETIC_LOCAL` 외 환경도 거부한다.
  이 환경 문자열 자체는 실제 파일/DB의 합성 여부를 증명하지 않는다.
- 결과에는 고정 사유만 포함하며 원문·root·DB 식별자·object key·포트 예외 내용을 내보내지 않는다.

**반환된 결과는 삭제 허가가 아니다.** 함수 반환 시 guard가 이미 해제되므로 이후 삭제에
재사용할 수 없다. 삭제 단계는 승인·객체·참조 재검사, 의도 기록, 삭제를 같은 유효한 보호
경계 안에서 수행하도록 별도로 구현·검증해야 한다.

현재는 합성 guard로 호출 순서와 실패 처리를 검증했다. 실제 writer 간 배타성이나
PostgreSQL 독립 transaction 경합을 검증한 결과가 아니다. Q1/Q2의 증거 공급,
Q3의 모든 writer 공유 잠금, Q4의 실제 승인 verifier 연결은 남아 있다.
실제 삭제·감사 저장·재시작 복구는 4~5단계 범위다.

### 3단계 실행 결과

```text
PYTHONPATH=. python -m pytest ai_worker/tests/rag/source_cleanup -q
72 passed (2단계 36 + 3단계 36)

PYTHONPATH=. python -m pytest ai_worker/tests/rag -q
914 passed

ruff check .
All checks passed!
ruff format . --check
534 files already formatted
MYPYPATH=backend:. python -m mypy backend/app ai_worker
Success: no issues found in 452 source files

git diff --check
통과
```

새 회귀는 승인 불일치·만료·철회, DB/namespace/객체 세대·bytes 교체, 불완전 관측,
새 직접/간접 참조, 활성 수집, guard 상실/오류를 포함한다. Python 3.13의 합성 포트 테스트다.
실제 승인 시스템·PostgreSQL 경합·운영 저장소·전체 CI는 실행하지 않았으며,
T01–T30 전체 완료 또는 실제 삭제 가능성을 의미하지 않는다.


## 4단계 — Local 합성 삭제·의도/결과 기록·복구

기준: `a1c6122`와 이 문서에 동반되는 4단계 변경. develop 기준 `a6e5645`이며,
최신 develop 병합·PR 생성·CI 실행 증빙은 아니다.

### 실행 경계

- `execution.execute_synthetic_batch`는 이전 `check_batch` 결과를 입력으로 받지 않는다.
  같은 guard 안에서 승인·대상·참조를 재검사하고 의도 기록 → 재검사 → 삭제 → 결과 기록을 수행한다.
- `SyntheticCleanupLab`만 실제 unlink를 구현한다. 생성자가 직접 만든 임시 디렉터리에
  고정된 합성 ASCII 데이터만 생성하며 기존 Source root·S3·DB를 입력받는 실행 경로가 없다.
  임의 namespace·대상·다른 환경·OCR 종류는 거부한다. API·CLI·scheduler에 등록하지 않았다.
- `RAW_RESPONSE`/`REJECTS` 종류를 내부 배치 hash에 추가했다. 형식은
  `source-cleanup-review-batch-v2`로 올렸고 기존 v1 승인 hash를 재사용하지 않는다.
  이는 정규 Catalog manifest나 공용 Source 상태 계약의 변경이 아니다.
- 합성 생성일은 fixture가 선언한 31일 전 시각이다. 실제로 31일 경과한 파일을 수집한 증거나
  물리 파일 생성 시각을 확보하는 Q1 구현이 아니다. reference count와 승인자 역시 합성 포트다.

### 삭제·감사·복구 동작

1. 전체 배치 hash와 승인·실행자를 검증하고 guard를 획득한다.
2. 객체별 bytes SHA-256·크기·세대·Source 소유·기간·전체 참조 조건을 검사한다.
3. append-only API로 INTENT를 JSONL에 쓰고 flush/fsync가 반환된 후에만 진행한다.
4. 느린 기록 중 상태가 바뀔 수 있어 승인·객체·참조·잠금을 다시 확인한다.
5. 동일 guard에서 객체를 다시 확인하고 descriptor-relative unlink 및 directory fsync를 수행한다.
6. DELETED 결과를 append/fsync한 경우만 해당 객체를 완료로 보고한다.

파일과 감사 기록은 한 transaction이 아니다. 삭제 예외는 실제 unlink 이후 발생할 수도 있어
UNKNOWN으로 기록한다. 일부 실패 시 성공 건을 복원하거나 다시 지우지 않는다.
재실행은 디스크의 이력을 새로 읽고, DELETED이며 객체가 없는 건만 건너뛴다.
성공 기록 뒤 같은 key에 객체가 다시 생겼으면 삭제하지 않는다.

INTENT만 남았는데 객체가 없으면 UNKNOWN 결과를 append하고 외부 확인이 필요한 상태로
남긴다. 과거 의도만으로 삭제 성공을 주장하지 않는다. 객체가 남았다면 명시적 retry 요청과
새 승인·참조·세대 검사를 통과해야 다시 시도한다. 손상/불완전 JSONL은 이후 삭제를 차단하며
복구를 이유로 기존 기록을 수정·삭제하지 않는다. 반환 결과가 실패여도 이미 삭제된 객체는
존재할 수 있으므로 재시작 시 반드시 디스크 이력을 확인한다.

재시도는 기본 1회이며 호출자가 명시할 때 합성 실행 안전 상한 3회까지만 허용한다.
이는 테스트 모델의 bounded 실행 제한으로, 운영 재시도 횟수·간격 정책(Q6)을 확정한 값이 아니다.
자동 재시도·장기 실패 자동 해제·UNKNOWN 수동 확정 API는 없다.

### 감사·잠금의 실제 보장 범위

- 기록: 배치 hash·안전한 객체 참조·시도 ID·종류·checksum·정책·승인 근거·실행자·시각·결과 코드.
  객체 key/root·payload·Provider 예외 원문을 기록하지 않는다. `references_verified`는 해당 시도의
  의도 기록 시점 검증 이력이며 재시작 시 현재 참조가 없다는 증거가 아니다.
- 파일 journal에는 append API만 있고 중복 시도·중복 결과·순서 오류를 거부한다.
  디렉터리 소유자의 파일 직접 수정/삭제를 OS나 DB 권한으로 막는 감사 불변성은 구현하지 않았다.
  T21의 운영 권한 검증은 아직 미완료다. 임시 fixture 정리는 테스트 종료 동작이며 감사 보존 정책이 아니다.
- 합성 executor끼리는 실제 flock으로 동시에 들어가지 못한다. fixture 외 실제 Source
  writer·재사용·DB 참조 생성 경로는 이 잠금에 참여하지 않는다. Q3의 운영 경합 해결은 아니다.
- 새 journal 인스턴스와 별도 Python 프로세스에서 기록을 다시 읽는 것을 검증했다.
  BaseException 중단 주입으로 삭제 후 결과 미기록 복구를 검증했지만 실제 host crash·전원 장애나
  전체 프로그램 재기동의 DB/승인/객체 포트 복구를 완료한 것은 아니다.

### 검증 결과

| 검사 | 결과 |
| --- | --- |
| Source cleanup 전체 | 102 passed (기존 72 + 신규 30) |
| Worker RAG 전체 | 944 passed |
| Ruff / format | 통과, 537 files |
| Mypy Backend·Worker | 통과, 455 source files |
| git diff --check | 통과 |

Python 3.13, 자동 생성한 Local 합성 파일만 사용했다. 실제 PostgreSQL 통합·운영 역할·
Source writer 동시 transaction·전체 CI는 실행하지 않았다.

| 정책 시나리오 | 4단계 증빙 | 남은 범위 |
| --- | --- | --- |
| T10 | 합성 대상 실제 unlink와 기록 | 실제 생성/소유·승인·전체 참조 증거 |
| T16 | 일부 실패 후 성공 건 유지·남은 건 재검사 | 실제 운영 실패 인계 |
| T17/T19 | 중단·결과 기록 실패 후 디스크 이력으로 UNKNOWN 복구 | 운영 조사·불명확 결과의 승인된 해소 절차 |
| T18 | INTENT 기록·fsync 실패 시 삭제 없음 | 운영 감사 저장소 권한/장애 |
| T20 | 재시도 전 참조·승인·bytes·세대 변경 차단 | 모든 Source writer의 공유 잠금 |
| T21 | append API, 중복/잘못된 순서·손상 이력 거부 | 비특권 UPDATE/DELETE 거부 및 운영 보존 |
| T22 | 오류 응답·파일 감사에 payload/key/root/Provider 예외 없음 | 운영 로깅·감사 전체 경로 |

4단계 합성 실행 모델의 완료이며 #347 전체 완료나 운영 삭제 준비 완료가 아니다.
5단계에서는 이 증빙과 미완료 조건을 T01–T30·runbook에 대조하고 DB/승인/잠금 인계를 정리한다.


## 5단계 — 최신 develop 통합·직접 참조 DB 검증·인계

기준 develop `76448ea`를 merge commit `a96acc3`으로 반영했다. #350/#344/#354/#345가 포함되며
#347 자체 migration은 추가하지 않았다. Alembic head는 `169b2c3d4e5f` 하나다.
일회용 PostgreSQL 16 DB에 최초 revision부터 이 head까지 upgrade를 실행했다.

`tests/integration/rag/test_source_cleanup_references.py` 8건으로 다음을 실제 migrated schema에서 확인했다.

- FAILED(snapshot NULL)·NO_CHANGE(snapshot 존재) Run의 RAW_RESPONSE/REJECTS 참조 보호.
- 서로 다른 Run/종류가 공유하는 동일 backend/key 전체 count.
- 무참조 0건도 namespace·전체 조회 범위 증명이 없어 HOLD 유지.
- 서로 다른 DB connection에서 미commit 참조는 0건으로 보이지만 HOLD이며, commit 후에는 보호됨.
- 조회 오류를 참조 0건으로 바꾸지 않고 고정 사유의 HOLD로 처리.

독립 Python 프로세스가 합성 flock을 잡은 동안 삭제가 시작되지 않고, 해제 후 실행되는
추가 회귀도 통과했다. 이는 실제 Source writer와 참조 생성의 경쟁을 해결하는 T14 증빙이 아니다.

### T01–T30 최종 대조 (이번 PR 기준)

`합성 통과`는 지정 fixture/포트 범위의 검증이다. `부분`은 운영 또는 미구현 참조·권한 경계가 남는다.

| ID | 상태 | 증빙 또는 남은 범위 |
| --- | --- | --- |
| T01 | 합성 통과 | 정책·시각·소유 증거 누락 보류, invalid batch 거부 |
| T02 | 범위 유지 | scheduler/Runtime 활성화 진입점 없음 |
| T03 | DB 통과 | 서로 다른 Run/종류의 동일 객체 count=2 보호 |
| T04 | DB 통과 | FAILED·snapshot NULL의 RAW_RESPONSE/REJECTS 참조 보호 |
| T05 | 부분 | NO_CHANGE 직접 참조 DB 검증. 과거 Catalog의 전체 관계 추적은 미구현 |
| T06 | 부분 | 합성 downstream 양수 보호. Citation/평가/외부 증빙 adapter 미연결 |
| T07 | 통과 | 실제 SQL 오류 및 합성 불완전 inventory/범위 보류 |
| T08 | 합성 통과 | 30일 경계·생성 시각 누락/미래/naive 보류. 실제 생성 증거는 미연결 |
| T09 | 부분 | 실제 commit 전 count=0도 HOLD. 진행 중 모든 writer 보호는 미연결 |
| T10 | 합성 통과 | 자체 임시 파일 삭제·의도/결과 fsync |
| T11 | 합성 통과 | 배치 hash·정책·범위 변경 차단 |
| T12 | 부분 | synthetic receipt 누락·불일치·만료/철회 차단. 실제 권한 verifier 미연결 |
| T13 | 합성 통과 | 의도 기록/재시도 중 새 참조 차단 |
| T14 | 미완료 | 실제 Source writer·재사용·신규 DB 참조의 공유 잠금 미연결 |
| T15 | 합성 통과 | 독립 프로세스 cleanup guard 배타성·성공 재호출 멱등 |
| T16 | 합성 통과 | 일부 실패 후 성공 유지·생존 대상 제한 재시도 |
| T17 | 합성 통과 | 삭제 예외/객체 부재를 UNKNOWN으로 보존 |
| T18 | 합성 통과 | INTENT 쓰기/fsync 실패 시 삭제 없음 |
| T19 | 부분 | 중단 주입·새 journal/독립 프로세스 읽기. 운영 재기동 포트/감사 복구 미연결 |
| T20 | 합성 통과 | 재시도 참조·승인·bytes/세대 교체 차단 |
| T21 | 부분 | append API·중복/손상 거부. OS/DB 비특권 변경·삭제 방지 미구현 |
| T22 | 합성 통과 | payload/root/key/Provider 예외 비노출 |
| T23 | 합성 통과 | 다른 DB/namespace 거부. 실제 환경 귀속 증거 미연결 |
| T24 | 부분 | 허용 종류·공유 직접 참조 보호. 실제 Source 소유 입증 미연결 |
| T25 | 부분 | 종료 예외 없음. 실제 종료·참조 인계 미수행 |
| T26 | 합성 경계 통과 | 생성한 fixture 외 store/batch 거부. S3 실행 포트 없음 |
| T27 | 제외·미실행 | S3 삭제 미구현. marker 의미의 모의 실행도 수행하지 않음 |
| T28 | 합성 통과 | 배치와 namespace/config 불일치 차단 |
| T29 | 합성 통과 | 실제 Local bytes·크기·세대 재검사, symlink 거부 |
| T30 | 부분 | 직접 행 전체 조회. 새 테이블/외부 참조 목록 자동 확인 미구현이며 SQL adapter는 불완전 유지 |

### 이슈 완료 판정

계획한 5단계의 **합성 구현·검증·인계 초안**까지 작성했다. #347 전체 종료 판정은 아니다.
실제 생성/소유·전체 참조·승인·모든 writer 잠금·감사 권한이 미연결이고,
실행자 및 감사 보관·관리·보존 종료 기준도 실제 담당자에게 인계되지 않았다.
따라서 #347은 Open을 유지하고 이번 PR에는 `Related #347`을 사용한다.
이 미완료 항목을 #166 또는 #164 normalization 작업이 해결한다고 가정하지 않는다.

[runbook·담당 인계표](../runbooks/source-artifact-cleanup-347.md)를 작성했다.
#335/#165/#323 연결용 초안은 별도 전달하며 댓글/PR은 자동 게시하지 않았다.


### 5단계 최종 실행 결과

| 검사 | 결과 |
| --- | --- |
| Source cleanup 합성 회귀 | 103 passed |
| Worker core·OCR·RAG·evaluation 전체 | 2,351 passed, 8 skipped (cleanup 포함) |
| migrated PostgreSQL 참조 조회 통합 | 8 passed |
| 전용 DB Alembic upgrade head | 성공, `169b2c3d4e5f` |
| Alembic heads | `169b2c3d4e5f` 단일 head |
| 전체 Ruff·format | 통과, 545 files |
| Mypy Backend·Worker | 통과, 459 source files |
| git diff --check | 통과 |

테스트는 Python 3.13, PostgreSQL 16의 전용 `source_cleanup347_test`만 사용했다.
테스트 컨테이너와 익명 볼륨은 종료 후 정리한다. 전체 Backend·Frontend·CI·migration downgrade
재실행·실제 권한/운영 삭제 검증은 포함하지 않았다. 위 합성/조회 결과를 해당 미실행 항목의
완료 증빙으로 사용하지 않는다. 실행 명령은 runbook과 기존 Worker 단위 테스트 명령을 따른다.
