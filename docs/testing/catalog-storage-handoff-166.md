# #166 저장 준비·복원 자료의 Candidate v2 인계

상태: **DB 비의존 인계 검증 완료 / PostgreSQL에서 조회한 자료의 인계는 미검증**.
기준: `f7726a7` 이후 이 문서와 함께 커밋한 6단계 검증 보강.
작성·구현: 김지혜. Candidate·RAG 리뷰: 정현우. DB 연결 리뷰: 송은영.

## 전달 경로와 실제 구현 상태

`prepare_catalog_storage` → `CatalogStoragePlan` → `restore_current_catalog_storage`
→ `CatalogExportArtifacts` → 공개 `build_candidate_index` 경로를 합성 자료로 검증한다.
이번 경로 사이에 SQL 저장/조회를 수행하는 adapter는 없다.

| 경계 | 인계 내용 | 현재 확인한 것 |
| --- | --- | --- |
| 저장 준비 | Identity·출처·구성원 링크·canonical record·hash 계산 자료 | 누락·중복·변조 거부, 조회 순서와 무관한 복원 |
| 복원 후 승인 | 기존 CatalogApprovalVerifier의 현재 응답 | 저장된 승인과 정확한 결속, 거부·조회 실패 시 반환 없음 |
| 공개 Candidate 입력 | CatalogExportArtifacts 전체 | manifest·JSONL·typed 값의 v2 결속. typed Catalog 단독 전달하지 않음 |
| Candidate가 받는 hash | 기존 catalog_manifest_hash = v2 envelope hash | 고정 fixture의 예상값과 실제 Candidate manifest 값 일치 |
| 사용자 공개 | 구현하지 않음 | Runtime·Production 활성화 없음 |

현재 승인 포트는 합성 응답으로 검증한다. 실승인 저장소의 권한·만료·회수 조회와 동시 철회
보장은 별도 연결 범위다. 복원 성공이나 Candidate 단위 build 성공을 운영 승인으로 해석하지 않는다.

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

저장소 루트에서 프로젝트 의존성을 설치한 환경으로 실행한다. DB·외부 승인 서비스는 필요 없다.

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

## 미완료 항목과 상태 해석

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
