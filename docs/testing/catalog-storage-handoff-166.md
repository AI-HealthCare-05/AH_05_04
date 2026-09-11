# #166 저장 준비·복원 자료의 Candidate v2 인계

상태: **PostgreSQL commit·read-back·Candidate v2 인계 구현 및 합성 검증 완료**.
작성·구현: 김지혜. Candidate·RAG 리뷰: 정현우. DB 연결 리뷰: 송은영.

## 전달 경로와 실제 구현 상태

`prepare_catalog_storage` → `SqlAlchemyCatalogBuildRepository.save_build()` → PostgreSQL commit
→ `load_build(set_id, approval_verifier=...)` → `CatalogExportArtifacts` → 공개 `build_candidate_index`.

- 저장 adapter가 transaction을 소유한다. 구성원·Identity·Set·출처·hash 자료를 함께 commit한다.
- 조회는 새 `REPEATABLE READ, READ ONLY` transaction을 사용한다. 누락된 행을 INSERT하거나 손상 값을 보정하지 않는다.
- 보존된 JSONL·manifest로 v2 typed 값을 복원하고, 실제 Identity·Product·Ingredient·Alias·Component·Search Entry,
  Source Snapshot/version, Set/member/hash 전체를 재대조한다. 정규화 과정에서 원문 `010.00`이나 NFD 표시 문자열을 바꾸지 않는다.
- 기존 `CatalogApprovalVerifier`를 호출마다 확인한다. verifier 없음·거부·저장 당시 승인과의 불일치 시 인계하지 않는다.
- 반환하는 것은 typed Catalog 단독이 아닌 JSONL·manifest를 포함한 전체 artifacts다. 기존 envelope hash 의미를 유지한다.
- 입력 적재의 실패·commit 이전 실패는 전체 rollback한다. commit 이후 확인 실패는 성공으로 응답하지 않으며,
  동일 내용 재호출 시 이미 commit된 Set을 대조·재사용한다. 내용이 충돌하면 기존 결과를 보존하고 실패한다.
- 여기서 Set 재사용은 확정된 v2 content Set 범위다. 새 실행 ID나 요청 key를 발급한 것이 아니며 자동 retry를 추가하지 않는다.
- Trigger·RLS·업무 DB 함수는 도입하지 않는다. ordinary FK·UNIQUE·CHECK와 Python 검증을 함께 유지한다.

## 유지하는 후속 범위

- #436 `dc745a4`의 실제 Snapshot Receipt provenance 검증을 저장·조회 경로에 연결했다. 선행 PR 리뷰·병합 후 재정렬은 남아 있다.
- D-02 실행 provenance, Authority projection/Runtime hash 등의 합의된 후속 경계는 그대로 유지한다.
  ingestion run·version·member_ref·hash로 정본 normalization 실행 ID를 대체하지 않는다.
- 현재 승인 포트는 합성 응답으로 검증한다. 실제 승인 저장소·권한·만료·회수 조회 및 동시 철회 보장은 별도 연결 범위다.
- 배포용 Catalog Writer 권한 연결은 후속이다. 이번 변경으로 신규 Catalog 테이블의 DB 권한을 광범위하게 부여하지 않는다.
- 저장 성공이나 합성 Candidate build 성공은 Runtime 활성화·Source 승인·Production 공개를 의미하지 않는다.

## PostgreSQL 재현과 검증

`tests/integration/rag/test_catalog_storage_roundtrip.py`는 매 검사에 독립 DB를 생성하고 실제 Alembic head를 적용한다.
테스트가 끝나면 해당 DB만 삭제한다. DB 생성 권한이 있는 **전용 테스트 PostgreSQL**을 사용해야 한다.

```bash
PYTHONPATH=backend:. uv run pytest tests/integration/rag/test_catalog_storage_roundtrip.py -q
PYTHONPATH=backend:. uv run pytest tests/migration/test_catalog_storage_migration.py -q
```

새 DB fixture `tests/fixtures/rag/catalog/db-v2/`는 실제 UUID 형태의 합성 Snapshot 2개,
제품 2개, 성분·Component 1개, 교차 Snapshot Alias 1개, NFD 표시 이름과 `010.00`을 포함한다.
정확한 JSONL·manifest·digest를 파일로 고정했으며 실행 중 기대값을 재생성하지 않는다.
기존 `hash-v2/` fixture는 변경하지 않았고 새 byte reader가 그대로 복원하는지 별도로 검증한다.

Catalog migration 순서는 최신 develop의 `3984b5c6d7e8 → 166a7b8c9d0e → 166b8c9d0e1f`다.
기존 과거 revision 회귀는 그대로 유지하고, #166 신규 migration 검사는 독립 DB에서 실행한다.
기존 Alias 등 근거 없이 변환할 수 없는 데이터는 계속 migration을 중단한다.

## 이번 단계 검증 결과

- Catalog·Candidate 단위 테스트: **284 passed**.
- 실제 commit·read-only 복원·변조 거부·승인 철회·동시 재시도 및 기존 Catalog 저장소: **38 passed**.
- 전체 migration suite: **169 passed** (#166 독립 DB 검사 11건 포함).
- head·보호 Writer 경계·Evidence/Citation의 Catalog fixture 회귀: **15 passed**.
- 관리 서비스 회귀: **28 passed** (승인 Alias 변경 차단 포함).
- Ruff, 전체 format, mypy **543개 파일**, DB 업무 로직 재도입·Python 쓰기 경계·테스트 분류 검사 통과.
- Alembic head **166b8c9d0e1f**, 실제 DB의 사용자 Trigger·RLS·제거 대상 함수 **0개**.

Backend·계약·선별 통합 전체 실행은 최초 **1718 passed / 59 skipped / 4 failed / 22 errors**였다.
실패 원인은 기존 head 고정 검사와 새 Catalog index에 필요한 테스트 DB의 pg_trgm 준비 누락이었다.
이를 수정한 뒤 관련 head·관리 경로를 재검증했다. 최초 전체 실행이 모두 통과했다고 표시하지 않는다.
Docker 이미지 계약 검사는 해당 종합 실행에서 통과했으며 AWS·팀 DB·운영 DB에는 적용하지 않았다.
이번 단계에서 원격 CI를 실행한 것으로 보고하지 않는다.

## 고정 재현 자료

저장소 루트 기준 `tests/fixtures/rag/catalog/hash-v2/`:

| 파일/값 | SHA-256 |
| --- | --- |
| catalog.jsonl / export checksum | `0d911d12d9424d8f603ae140cbbd266ed21ceb72e9f8e59b3b2c5f75426601f0` |
| envelope-payload.json / Catalog envelope hash | `bfeb5a407629b772b05a207490f97b110e297203e82d3f8cb16a6f1b106005f0` |
| manifest.json / 전달 파일 checksum | `87d399ce21952dc97c86d50732bd027fc92f81a442f92d8de8b142e6370a216e` |

세 번째 값은 파일 검증용이며 신규 API 필드가 아니다. Candidate projection hash나 Runtime
medication Catalog manifest hash를 계산했다고 주장하지 않는다. 현행 v2 의미를 유지한다.

합성 입력은 공식 제품 Identity 2개, Product name 2개, 교차 Snapshot 출처의 승인 Alias 1개,
Source 2개와 NFD 제품 표시값을 포함한다. 정상/역순 저장 자료와 Source 승인 순서 모두에서
복원된 파일 bytes·digest가 고정값과 같고, Candidate에 제품 Identity 2개·이름 2개·승인 Alias
1개 및 NFD 원문이 유지되는지 확인한다. 기대값은 테스트 실행 중 생산 함수로 재생성하지 않는다.

## 재현 명령

아래는 DB 비의존 검사다. PostgreSQL 검사는 위 별도 명령을 사용한다.

```bash
PYTHONPATH=backend:. uv run pytest ai_worker/tests/rag/catalog ai_worker/tests/rag/test_candidate_index.py -q
shasum -a 256 tests/fixtures/rag/catalog/hash-v2/catalog.jsonl tests/fixtures/rag/catalog/hash-v2/envelope-payload.json tests/fixtures/rag/catalog/hash-v2/manifest.json
```

결과: **278 passed**. Python 3.13. 관련 Ruff·변경 Python format·RAG Mypy와 diff 공백 검사를 수행한다.

## 최신 develop 반영 후 재검증

- 확인일: 2026-09-08.
- 기준 코드: `07506ac` (develop `b6e99ad` 병합). #358 Evaluation 승인 provenance와
  #351 데모 배포 변경을 충돌 없이 반영했다. 운영 배포를 수행한 것은 아니다.
- `PYTHONPATH=backend:. pytest ai_worker/tests/rag -q`: **1016 passed**.
- RAG 구현·테스트 Ruff: 통과. RAG Mypy: **38 source files 통과**.
- Alembic ScriptDirectory 탐색: **29 revisions, 단일 head `169b2c3d4e5f`**.
  실제 PostgreSQL upgrade/downgrade 검증과 구분한다.
- #355는 조회 시 미병합이며, 이번 develop 반영으로 D-02 인계나 Evidence/Citation
  선행 migration 병합이 충족됐다고 판단하지 않는다. 실제 migration 부모는 착수 직전에 재확인한다.

## 당시 미완료 항목과 상태 해석 — DB 구현 전 과거 기록

- D-02는 **미확정 그대로**다. 별도 run 신설·ingestion run을 정본 normalization_run_id로 대체하는 변경 없음.
- schema·migration·실제 Worker PostgreSQL adapter·commit/rollback·재시도·부분 공개 방지는 미구현/미검증.
- 실제 DB에서 읽은 자료의 Candidate 인계는 adapter 연결 후 같은 fixture와 경로로 다시 검증해야 한다.
- D-03·D-04·D-06은 [Proposed 연결안](../contracts/proposed/post-mvp-1/catalog-db-integration-v2.md)의 리뷰 대상이다.
- #164·#165 이슈 종료를 D-02 자동 확정으로 해석하지 않는다. 합의된 migration 순서 역시 적용 직전에 확인한다.
- 6단계 중 독립적인 인계 증빙·PR 초안 준비까지만 완료했으며 #166을 종료하지 않는다.

[현재 #167 설계](../designs/ceohwj/issue-167-rag-candidate-index-design.md)와
[구현 계획](../designs/ceohwj/issue-167-rag-candidate-index-implementation-plan.md)의 공개 입력은
이미 CatalogExportArtifacts/v2로 정렬돼 있다. 해당 담당 문서를 불필요하게 변경하지 않았다.
`docs/validation/rag/catalog/synthetic-catalog-v1`은 과거 자료로 보존한다.


## 2026-09-11 — Source Receipt 연결과 저장 후 전체 검증

- Source `get_snapshot_receipt()`를 실제 DB에서 조회하고 ID/version 및 `validate_provenance()`를 검증한다.
- 저장에서는 Snapshot 잠금 이후 Identity 쓰기 전에 검증한다. 조회에서는 같은 read-only repeatable-read
  transaction에서 보존 bytes와 실제 Source Receipt·구성원·Set·hash를 검증한다.
- commit 이후 manifest/Set 대조만 하던 경로를 전체 read-back으로 통일했다. 구성원 변조나 Source
  provenance 손상이 발생하면 저장 성공을 반환하지 않으며, 이미 commit된 데이터를 임의 복구하지 않는다.
- 승인 verifier는 그대로 별도 호출한다. 실제 승인 저장소·철회 경합·Freshness 구현 완료를 뜻하지 않는다.
- 기존 `db-v2`와 `hash-v2` golden 자료는 보존했다. `db-receipt-v2`는 동일 합성 Catalog에 유효한
  `external:v1/v2` Source 참조를 결속한 고정 bytes/digest다. 기대값은 테스트 실행 중 재생성하지 않는다.
- 신규 merge revision `166d0e1f2031`은 기존 `166c9d0e1f20` 및 `362c3d4e5f60` 이력을 합친다.
  과거 migration을 수정하지 않고 schema/data 변경이나 Trigger/RLS/업무 DB 함수 없이 단일 head를 만든다.
- 확정 v2 입력·출력은 유지한다. D-02·D-03 Authority Set·D-04 Authority 차이 결정·D-05 후속 hash와
  실제 승인/감사 저장소·Catalog Writer 연결이 모두 완료됐다고 해석하지 않는다.

- 이미 transaction이 열린 외부 connection 주입은 첫 Catalog 쓰기 전에 거부한다. 기존 Backend
  테스트의 외부 transaction/savepoint를 commit 증빙으로 사용하지 않는다. 실제 commit·재사용은
  독립 PostgreSQL roundtrip 테스트가 검증하며, Backend 테스트는 외부 transaction 거부·부분 쓰기
  0건을 확인한다.

### 최종 검증 결과

구현 기준: `a82043a` (선행 통합 `f0f5a6f`). 전용 PostgreSQL 17·Redis 7과 합성 데이터만 사용했다.

- `scripts/ci/run_test.sh`: **exit 0**.
- 전체 migration: **192 passed**.
- Backend·계약·PostgreSQL 통합: **1815 passed, 59 skipped**.
- Redis 통합: **23 passed**.
- Worker 전체: **2903 passed, 8 skipped**.
- 통합 coverage: **94%**.
- 집중 검증: Catalog·Candidate 단위 **284 passed**, 실제 DB roundtrip **20 passed**,
  Backend Catalog 저장소 **25 passed**. 전체 CI와 중복되는 수치는 합산하지 않는다.
- Ruff 전체·format(713 files)·Mypy(553 source files)·diff 검사 통과.
- DB 업무 로직 재도입·보호 테이블 Python 쓰기·테스트 inventory 검사 통과.
- 최신 head **166d0e1f2031**, 실제 DB의 사용자 Trigger·RLS·제거 대상 함수 **0개**.

첫 전체 실행에서 #436의 과거 head 고정 테스트 2건을 발견해 현재 단일 head/실제 DB 비교로 수정했다.
다음 실행의 외부 transaction을 commit으로 취급하던 Catalog 테스트는 소유권 거부 테스트로 정렬했고,
임시 환경의 Redis host/port는 Worker 기본값과 통합 실행용 값으로 구분했다. 위 수치는 이 수정 이후
전체 스크립트를 다시 실행한 최종 결과다. 실제 commit·재사용 증빙은 독립 DB roundtrip에서 유지한다.

원격 push·CI·담당자 승인·운영 적용은 이번 로컬 검증에 포함하지 않는다. 실제 승인 저장소/철회,
실패 감사 저장소와 Catalog Writer 연결 및 합의된 후속 계약이 남아 있으므로 #372는 Draft 범위다.
