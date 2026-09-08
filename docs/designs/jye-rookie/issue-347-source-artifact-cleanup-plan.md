# #347 Source Artifact 수동 정리 — 1단계 구현 경계 조사

- 작성: 김지혜 (`Jye-rookie`), 2026-09-08
- 기준 코드: develop `a6e5645` (#329 병합 포함)
- 상태: 4단계 Local 합성 삭제·파일 감사·복구 모델까지 진행. 실제 Source/승인/공유 잠금/감사 DB 연결과 5단계 인계는 미완료. 단계별 기록은 하단 참조.
- 관련: [#347](https://github.com/AI-HealthCare-05/AH_05_04/issues/347), [#335](https://github.com/AI-HealthCare-05/AH_05_04/issues/335), [PR #348](https://github.com/AI-HealthCare-05/AH_05_04/pull/348), #165/#323
- 정책 근거: [Source 보존·삭제 정책](../../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md)
- DB·보안: 송은영, Source·provenance: 정현우, 정책·배치 승인: 권가빈. 운영 실행자는 별도 지정한다.

이 문서는 구현 순서와 현재 코드의 차이를 기록한다. 정책 정본을 복제하거나 신규 테이블·enum·API를
확정하는 문서가 아니다. #348은 병합됐지만 정책 파일은 여전히 Proposed 표기를 사용한다.
문서 병합·리뷰 확인과 Target 승격, 실제 배치 승인은 구분하며 이 단계에서 자동 승격하지 않는다.
정기 자동 삭제·실제 Source Runtime 활성화·운영 저장소 실행은 범위 밖이다.

## 현재 코드 대조

| 영역 | 확인한 구현 | #347에 필요한 부분 |
| --- | --- | --- |
| 저장 포트 | `RawArtifactStore.put_verified`만 존재 | 읽기 전용 조사 포트를 별도 정의. 삭제 메서드를 조사 단계에 제공하지 않음 |
| Local 객체 | root 아래 내용 주소 key, 원자적 hard link 공개, 기존 객체 재사용·실제 bytes 검증 | root/DB 결속, 객체 목록 완전성, symlink·임시 파일 제외, 생성·소유 근거 |
| S3 객체 | config의 bucket/prefix/endpoint, put/head 및 checksum metadata 검사 | 이번 실행 구현은 Local 합성만. S3 head/ETag를 실제 bytes 또는 생성 시각 증명으로 간주하지 않음 |
| 객체 참조 | `rag_source_ingestion_artifact`의 backend/key index는 비unique | 동일 backend/key 전체 행 조회. Run/Snapshot 상태 필터 금지 |
| 생성 시각 | Artifact의 `created_at`은 DB 행 생성 시각. StoredRawArtifact는 객체 생성 시각을 반환하지 않음 | DB 행 시각·mtime·ctime을 물리 객체 생성 시각으로 임의 대체하지 않음 |
| namespace | DB 행은 bucket/root를 갖지 않음 | 접근 통제된 배치 정보에 DB·Local 절대 root·안전한 namespace를 함께 고정 |
| 파일/DB 순서 | 파일 보존 후 DB 저장, rollback 후 파일이 남을 수 있음 | DB 무참조 파일도 Source 소유·생성 시각을 증명할 수 없으면 보류 |
| 잠금 | `try_lock_acquisition`은 Source 행 잠금, 별도 operation 잠금 존재 | 여러 Source가 공유하는 동일 객체와 cleanup 사이의 보호 수단 검토 필요 |
| 감사·복구 | Source cleanup 전용 의도·결과 저장소 없음 | 별도 저장 경계·권한·append-only·재시작 복구 설계 및 검토 |

코드 근거(저장소 기준 상대 경로):

- `ai_worker/tasks/rag/source_ingestion/artifacts.py`
- `ai_worker/tasks/rag/source_ingestion/persistence.py`
- `ai_worker/adapters/local_private_source_artifact_store.py`
- `ai_worker/adapters/s3_private_source_artifact_store.py`
- `ai_worker/adapters/sqlalchemy_source_snapshot_repository.py`
- `backend/app/models/rag_source.py`, `backend/app/models/rag_catalog.py`

## 참조 보호 기준선

1. 직접 참조는 `rag_source_ingestion_artifact`의 동일 `(storage_backend, object_key)` 전체 행이다.
   namespace 컬럼이 없으므로 다른 bucket/root의 행이라고 추정해 제외하지 않는다.
2. Artifact → ingestion run → snapshot → Catalog/Verification은 간접 보호 근거다.
   FAILED Run의 snapshot NULL, NO_CHANGE Run의 기존 Snapshot 재사용도 직접 참조 보호를 해제하지 않는다.
3. 참조 행은 append-only다. 후보를 만들려고 먼저 행을 삭제하거나 무효화하지 않는다.
4. Runtime·Citation·평가 등의 미구현 FK, 문자열 ref·외부 증빙은 자동으로 무참조가 아니다.
   실행 스키마/외부 참조 조사 범위를 증명할 수 없으면 판정은 불완전이며 보류한다.
5. DB 조회 오류·권한 부족·목록 일부 조회·새 직접 참조 테이블 미반영은 0건 결과와 구분한다.

## 단계별 입출력 초안

아래 이름은 설명용이며 public DTO·DB 컬럼·함수 signature가 아니다.

| 단계 | 입력 | 출력과 중단 조건 |
| --- | --- | --- |
| 1 조사 | 정책, 기준 commit, 모델·저장 경로 | 이 문서, 기존 구현/공백/추가 검토 목록 |
| 2 후보 조회 | 정책 근거, DB·namespace 결속, 객체 소유·생성·동일성 증거, 기준 시각, 전체 참조 조사 | 검토용 후보·보호·보류 사유. 삭제 자격이나 승인 토큰은 발행하지 않음 |
| 3 승인·최종 보호 | 고정 배치, 승인자·범위·유효성 증거, 재조회, 실제 bytes 검사, 공유 경합 보호 | 조건 충족 여부. 새 참조·대상 교체·잠금 상실·불확실성은 삭제 차단 |
| 4 삭제·감사·복구 | Local 합성 대상, 실행 전 의도 기록, 객체별 시도 식별 | 결과 append. 성공 건 유지, 실패·불명확 건만 재조사. 감사 실패 시 완료 선언 금지 |
| 5 통합·인계 | T01–T30 결과, PostgreSQL·Local 합성 실행 증빙 | PR·runbook, 미완료 운영 조건 및 실행자·감사 인계 |

### 2단계 구현 가능한 범위

- 내부 순수 판정과 읽기 전용 포트를 먼저 만든다. 합성 fixture가 제공한 사실과 실제 adapter가
  증명한 사실을 구분하며, 호출자가 임의 boolean만 넣어 운영 삭제를 승인하는 구조로 연결하지 않는다.
- Local 조사에서는 승인된 root 밖·symlink·`.pending-*`·소유 불명 객체를 후보로 승격하지 않는다.
- 기준 시각은 timezone-aware UTC로 비교한다. 정책 T08에 따라 정확히 30일인 경계도 보류하고
  30일을 초과했을 때만 다음 조건을 검사하는 구현안을 사용한다. 미래·naive·누락 시각은 보류한다.
- 생성 시각과 Source 소유 증명 포맷이 정해지기 전 실제 잔존 파일은 보류할 수 있다.
  합성 증거를 사용한 판정 테스트 통과가 실제 잔존 파일의 적격성 증명은 아니다.
- 조회 결과에는 원문 bytes·credential·Provider 오류를 넣지 않는다. 실제 DB/root/key 정보는
  접근 통제된 조사 자료에만 두고 일반 로그에는 안전한 식별자·고정 사유만 노출한다.
- 새 DB schema 없이 현재 Artifact 전체 참조 조회는 구현할 수 있다. 운영 삭제·감사 테이블은
  2단계에 추가하지 않는다. Worker는 Backend ORM을 import하지 않는 기존 adapter 경계를 따른다.

## 후속 구현 전 검토할 결정

| ID | 필요한 결정 | 현재 판단·검토자 | 막는 범위 |
| --- | --- | --- | --- |
| Q1 | 객체 최초 생성·재생성·Source 소유 증거 | 현 저장 metadata로 충분하지 않음. 별도 보존 기록 등 방식은 지혜 제안, 은영·현우 검토 | 실제 잔존 객체의 후보 확정 |
| Q2 | DB와 저장소 귀속 증거 및 전체 참조 조사 목록 | 공유 DB·과거 config 변경·외부 증빙 포함 범위 확정, 은영·현우 검토 | 실제 환경 무참조 확정 |
| Q3 | 객체 쓰기/재사용/참조 생성과 삭제 사이 경합 보호 | Source lock만으로 충족한다고 가정하지 않음. 모든 writer 경로·장애 복구 포함, 은영·현우 검토 | 3단계 실행 보호 연결 |
| Q4 | 배치 식별·승인 유효성·변경 시 재승인 | 정책 version·DB·namespace·객체 목록 및 실제 승인자에 결속, 가빈·은영 검토 | 삭제 실행 승인 |
| Q5 | 의도·결과 감사 저장소와 권한 | Source 전용 append-only. 테이블·migration·역할은 은영 협의 후 확정 | 4단계 영속 감사·복구 |
| Q6 | 제한 재시도·불명확 결과·종료 후 인계 | 무한 자동 재시도 금지. 실행자·관리자·장기 실패 처리·보관 위치 인계 | 운영 실행·최종 인계 |

위 항목은 PM에게 30일 정책을 다시 묻는 요청이 아니라 그 정책을 실제로 보장하기 위한 기술·운영
상세다. 이번 1단계에서 댓글을 게시하거나 답변을 받았다고 기록하지 않는다. #164 normalization
정렬은 #347 시작 조건이 아니지만 실제 참조 모델이 바뀌면 Q2 목록과 회귀를 갱신해야 한다.

## T01–T30 검증 배치

| 구현 단계 | 주 검증 항목 | 비고 |
| --- | --- | --- |
| 2 후보 조회 | T01–T08, T23–T25, T30 | 무참조·기간만으로 승인하지 않음. T06/T30은 실 DB·관계 조사까지 5단계에서 완결 |
| 3 승인·경합 | T09, T11–T15, T20, T26, T28–T29 | 수집·cleanup 독립 transaction과 장애 주입 필요 |
| 4 삭제·감사 | T10, T16–T19, T21–T22 | Local 합성, 의도/결과 이력과 재시작 검증 |
| 5 전체·인계 | T01–T30 대조, T27 | S3 운영 삭제는 제외. T27은 marker 의미를 영구 삭제로 보고하지 않는 모의 검증 |

1단계는 문서 조사이므로 위 테스트를 실행한 단계가 아니다. 코드 테스트 수치를 과거 #323/#348
증빙에서 복사하지 않는다. 실제 환경·운영 S3·기존 DB는 조회하거나 변경하지 않았다.

## 1단계 확인 결과

- [x] 정책/이슈와 담당·리뷰 범위 연결
- [x] 현재 저장·참조·잠금·시간·감사 공백 대조
- [x] 2단계 입출력·보호·보류 경계 및 결정 항목 분리
- [x] T01–T30을 후속 단계에 빠짐없이 배치
- [x] 문서 상대 링크·코드 경로 존재 및 `git diff --check` 확인
- [x] 읽기 전용 코드·합성 회귀 (2단계)
- [ ] 승인·경합·삭제·감사·통합 구현 (3~5단계)

## 2단계 진행 기록

읽기 전용 Local inventory·SQL 직접 참조 reader와 내부 조사 판정을 구현했다.
[구현·검증·미완료 경계](../../testing/source-artifact-cleanup-347.md)를 참고한다.
Q1/Q2의 실제 증거 공급이 없으므로 현재 실제 adapter는 무참조 파일도 보류한다.
Q3~Q6은 미확정 상태 그대로이며 삭제·감사 기능을 추가하지 않았다.


## 3단계 진행 기록

고정 배치 hash, 승인 증거 대조, 보호 경계 내부의 객체·참조 재검사를 구현했다.
합성 포트에서 승인 변경·만료·철회와 관측/참조 변화·잠금 상실을 검사한다.
[검증 문서의 3단계 절](../../testing/source-artifact-cleanup-347.md)에 실행 결과를 기록했다.

현재 결과는 dry-run이며 함수 반환 후 삭제 권한으로 사용할 수 없다.
실제 공유 lock이나 승인 adapter는 추가하지 않았으므로 Q3/Q4의 운영 연결 완료가 아니다.
공유 Source writer·DB 계약 변경 없이 검토 가능한 내부 구현까지 진행했으며,
실제 생성/소유 증거·전체 참조 조사·승인 저장소·경합 보호 방식은 Q1~Q4 검토를 거쳐 연결한다.
정책 Proposed 상태와 Source Runtime/자동 삭제 비활성은 유지한다.


## 4단계 진행 기록

임시 합성 파일에 한정한 실행 포트와 Local 실험용 adapter를 추가했다.
동일 guard 내부에서 의도 fsync → 승인·참조·객체 재검사 → unlink → 결과 fsync를 수행한다.
부분 실패·중단·감사 쓰기 실패·재시도 시 새 참조 및 객체 교체를 합성 회귀로 검증했다.

[4단계 증빙](../../testing/source-artifact-cleanup-347.md)에 실제 보장과 한계를 기록했다.
운영 감사 DB·역할·모든 Source writer 공유 잠금을 확정한 것이 아니며 Q1~Q6의 운영 인계는 남는다.
5단계의 DB/동시 실행·T01–T30·runbook 검토에서 미실행 항목을 완료로 처리하지 않는다.
