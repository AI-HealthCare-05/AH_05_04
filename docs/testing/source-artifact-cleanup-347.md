# #347 읽기 전용 조사·배치 사전 검사 검증

- 구현: 김지혜. DB·보안 검토: 송은영, Source·provenance 검토: 정현우, 정책·배치 승인: 권가빈.
- 기준: `8da32d7` 및 이 문서와 함께 커밋하는 2단계 변경. develop 기준은 `a6e5645`.
- 정책: [#335 보존·삭제 정책](../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md)
- 계획: [1단계 경계 조사](../designs/jye-rookie/issue-347-source-artifact-cleanup-plan.md)
- 현재 상태: 2단계 조사와 3단계 내부 승인 결속·보호 경계 사전 검사 구현. 실제 승인 저장소·공유 잠금·삭제·감사 저장은 미구현.
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
