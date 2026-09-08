# #166 4단계 migration 인계 점검

- 상태: **착수 전 점검·변경안 준비 완료 / schema·migration 구현 미착수**.
- 기준일: 2026-09-08. 원격 develop `b6e99ad`, develop 병합 커밋 `07506ac`.
- 구현 담당: 김지혜. DB 리뷰: 송은영. Candidate·RAG 계약 리뷰: 정현우.
- 새 revision과 기존 FK 변경을 하지 않았다. 단계 완료나 DB 통합 성공으로 해석하지 않는다.

## 선행조건 확인 결과

| 조건 | 확인 결과 | 다음 처리 |
| --- | --- | --- |
| D-02 실행 참조 인계 | 사용자 최신 합의는 미확정. 별도 run 신설·ingestion run 대체 금지 | 실행/Publication key·FK·재처리 관계 인계 후 최종 연결 |
| 최신 develop | 원격 develop b6e99ad를 07506ac에 병합. #358·#351 추가 반영 | migration 착수 직전 다시 fetch |
| 현재 Alembic | revision 29개 탐색 성공, head는 169b2c3d4e5f 하나 | 이 값을 최종 부모로 예약하지 않음 |
| 기존 Catalog 모델 | Product 12·Ingredient 8·Alias 9·Component 10컬럼, normalization_run_id 없음 | 부재를 nullable 임시 컬럼이나 가짜 ID로 메우지 않음 |
| Runtime/Evidence/Citation | 기준 develop에 해당 기반 없음. #355 Runtime PR은 조회 시 open·미병합 | 합의된 Evidence/Citation 선행 병합과 head 인계 확인 필요 |
| D-03·D-04·D-06 | 구체 제안은 별도 Proposed 문서에 기록, 공유 DB 최종안 아님 | 실제 Source 근거 및 지정 리뷰어 검토와 연결 |

확인 근거: [#166 논의](https://github.com/AI-HealthCare-05/AH_05_04/issues/166),
[#355](https://github.com/AI-HealthCare-05/AH_05_04/pull/355), 병합된 모델·migration 및 사용자가 전달한
최신 D-02 합의·D-05 v2 문서. 이슈 Close 여부를 계약 인계로 대신하지 않는다.
GitHub 공개 댓글에 없는 비공개 협의 원문을 임의로 게시하지 않는다.

## 컬럼·제약 전환 순서안

아래는 인계 후 구현 순서다. 물리 테이블명·실행 FK·Set 상태를 이 문서에서 확정하지 않는다.

| 순서 | 현재 구조와 제약 | 변경 준비 | 이행 전 조사·검증 |
| --- | --- | --- | --- |
| 1 | 안정 Identity 테이블 없음 | 공식 type·code system·code 식별과 upsert 제약 | 기존 Product/Ingredient 코드 누락·허용 체계·상충 정의, 이름을 코드로 대체하지 않음 |
| 2 | Snapshot별 Product/Ingredient 행 | 기존 테이블에 안정 Identity와 인계된 Publication/실행 참조 연결 | 기존 행의 실제 provenance 복원 가능성, 같은 Snapshot 재처리 충돌, 임의 실행 생성 금지 |
| 3 | Ingredient의 Snapshot·정규화 이름 unique | 공식 Identity·Publication 중심 제약으로 교체 제안 | 동명·다른 공식 코드 보존. 코드 없는 기존 행은 별도 검토 후 이행 |
| 4 | Alias 대상·Snapshot composite FK, is_approved 단일 축 | 안정 대상 Identity와 자체 출처·상태·유효성 분리 | 기존 true만으로 승인 receipt 생성 금지. source_alias_ref는 현재 v2 alias_ref에 hash 결속돼 있을 뿐 원문 키로 복원되지 않음 |
| 5 | Component 같은 Snapshot FK와 product·ingredient·role unique | 현재 의미 보존, 확정된 실행 key 결속 및 문자열 함량 보존 컬럼 검토 | role 단일성 실데이터, 원본 키 확보, 숫자 반올림·정규화로 v2 export 변형 금지 |
| 6 | Search Entry·Set/member 없음 | 정본 Set 범위 확정 후 적격 Entry 및 구성원 연결 | 제품명/Alias형 nullable 참조 규칙, Alias 교차 Snapshot·Identity 일치, 다른 Catalog 구성 혼합 거부 |
| 7 | Catalog manifest 저장 없음 | 종류·schema/spec·값·불변 계산 대상 보존 | DB 복원 bytes와 v2 golden fixture 일치, hash 단독 FK·종류 대체 금지 |
| 8 | 승인·READY·실패 저장 원자성 미연결 | D-06 transaction과 DB/adapter 책임 반영 | 중간 실패·동시 적재·commit 불명확 재조회·부분 공개 방지 |

`CatalogStoragePlan`의 row/ref를 정본 Publication이나 Set으로 그대로 치환하지 않는다.
Snapshot별 참조 hash만으로 다른 normalization 실행을 구분할 수 없다는 D-02 문제를 그대로 유지한다.
Catalog manifest 전용 Runtime 컬럼은 별도 hash 계약 전환 조건을 충족한 후 검토한다.
기존 envelope hash를 Runtime medication hash로 저장하지 않는다.

## 인계 후 실제 migration 검증

1. 실제 데이터가 비어 있다고 가정하지 않고, 격리 복제본에서 건수·누락·상충·참조 구조를 점검한다.
   환자 원문이나 Source 표시 문자열을 조사 로그에 덤프하지 않는다.
2. 새로운 컬럼 도입 → 근거 기반 이행 → 검증 → 제약 전환의 순서를 설계한다.
   근거 없는 provenance·승인·원본 키를 가짜 값으로 채우지 않는다.
3. 최신 공통 head 이후 revision을 만들고 빈 DB upgrade와 기존 합성 데이터 이행을 검증한다.
4. downgrade의 손실·불변성 영향을 검토해 허용 범위와 거부 조건을 명시한다. 테스트 통과를 위해
   Source 상태 보호나 감사 제약을 제거하지 않는다.
5. PostgreSQL FK·unique·CHECK·불변성 테스트와 실제 Worker adapter commit/rollback을 별도로 실행한다.
6. #165 충돌·FAILED/NO_CHANGE 및 계보 회귀와 Candidate 공개 v2 인계를 함께 검증한다.

## 이번 점검에서 실행한 것과 한계

- Alembic ScriptDirectory로 revision graph 및 단일 head 확인.
- 합성 설정값으로 SQLAlchemy 모델 metadata를 읽어 현재 컬럼 구성 확인.
- 원격 develop과 관련 PR·이슈 본문/댓글 조회.
- 실제 PostgreSQL 연결·DDL·데이터 이행·upgrade/downgrade는 실행하지 않음.

따라서 4단계는 인계 대기다. 인계가 확인되면 이 문서의 기준 SHA·head·상태를 갱신하고
실제 migration과 테스트를 구현한다.

## #329에서 받은 Candidate 공개 인계 리뷰 유지

사용자가 추가로 전달한 현우님 리뷰는 후속 DB 작업에도 적용한다.

- 공개 builder 입력은 `CatalogExportArtifacts`, schema는 `medication-catalog-v2`다.
- `CandidateCatalogExport`는 내부 typed 값이다. DB에서 읽은 typed 값만 공개 builder에 넘기지 않는다.
- DB 조회 결과로 manifest·JSONL·typed Catalog를 함께 복원해 전체 인계 검증을 거친다.
- #167 설계·구현 계획, #166 검증 문서, 실제 코드가 같은 계약을 설명하는지 PR 완료 전 대조한다.
- 과거 v1 결과와 테스트 수치는 이력으로 보존하고 현재 v2 계약·증빙과 분리한다.

기준 develop에서는 아래 문서의 상단/공개 입력 절에 이 정정이 이미 반영되어 있음을 확인했다.
새 버전으로 되돌리거나 같은 정정을 중복 수행하지 않고 후속 변경 시 회귀 기준으로 유지한다.

- [#167 설계](../ceohwj/issue-167-rag-candidate-index-design.md)
- [#167 구현 계획](../ceohwj/issue-167-rag-candidate-index-implementation-plan.md)
- [#166 검증 기록](../../testing/catalog-build-166.md)

## 선행 가능한 구현 추가: 기존 데이터 집계

기존 네 Catalog 테이블의 행수·성분 Identity 누락·Alias boolean·Component 원문 누락·복수 role을
읽기 전용으로 집계하는 SQL을 추가했다. [실행·해석·검증 기록](../../testing/catalog-migration-preflight-166.md)에
이행 대상 복제본에서 재현할 방법을 기록한다. 정상적인 교차 Snapshot Identity 재사용은 오류와 구분한다.

기존 head를 적용한 격리 PostgreSQL의 합성 데이터로 검증했으며, 실제 이행 대상 DB의 조사 결과는
아직 확보하지 않았다. 이 점검 구현으로 D-02나 4단계 migration 착수 조건이 충족되는 것은 아니다.
