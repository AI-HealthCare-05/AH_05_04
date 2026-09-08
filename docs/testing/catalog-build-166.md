# Issue #166 Catalog build 검증 기록

## 현재 인계 계약

PR #329의 현재 공개 입력은 `CatalogExportArtifacts`, schema는 `medication-catalog-v2`다.
manifest·JSONL·typed Catalog 결속과 승인 gate를 검증한 뒤 Candidate Index를 생성한다.
`CandidateCatalogExport`는 내부 typed 값이며 단독 공개 입력이 아니다.
현행 계약: `docs/contracts/targets/post-mvp-1/catalog-build-v2.md`.

## 최초 구현 기록 (v1, 과거 검증 기준)

아래 v1 산출물과 최초 테스트 수치는 초기 구현 증빙이다. 현재 v2 승인·인계 검증 증빙으로 사용하지 않는다.
이후 날짜별 보완 기록과 문서 마지막의 최신 검증 결과를 함께 확인한다.

### 당시 구현된 범위

- 공식 제품·성분 Identity는 `entity_type + code_system + canonical_code`로 구성한다.
- Product와 Ingredient에 Source Snapshot과 Source record key를 보존한다.
- Product Component 자연키는 `(product, ingredient, component_role)`로 유지한다.
- 원문 표시 문자열과 검색용 정규화 문자열을 분리한다.
- 승인되고 활성 상태이며 효력이 있는 Product Alias만 Search Entry로 만든다.
- 비활성 Product, Ingredient Alias, 미승인·비활성·효력 없는 Alias는 실행용 검색 항목에서 제외한다.
- HIRA code system은 P0 Product·Ingredient Identity 및 Search Entry에서 제외한다.
- 중복 공식 Identity, 고아 Component, 여러 활성 Product를 가리키는 동일 정규화 Alias를 저장 전에 차단한다.
- 검증에 실패한 Catalog는 저장 포트를 호출하지 않고 manifest와 JSONL도 만들지 않는다.
- 구성원 입력 순서와 무관하게 canonical JSONL, export SHA-256, manifest SHA-256을 재현한다.
- Candidate Index에는 `CandidateCatalogExport` 타입과 manifest hash를 인계한다.

## 검증 결과와 고정 실패 코드

| 검증 | 결과 | 실패 코드 |
| --- | --- | --- |
| Product 공식 Identity 중복 | 활성 후보 차단 | `DUPLICATE_PRODUCT_IDENTITY` |
| Ingredient 공식 Identity 중복 | 활성 후보 차단 | `DUPLICATE_INGREDIENT_IDENTITY` |
| Product 또는 Ingredient가 없는 Component | 활성 후보 차단 | `REFERENTIAL_INTEGRITY_INVALID` |
| 동일 Alias가 서로 다른 활성 Product를 가리킴 | 자동 병합 없이 활성 후보 차단 | `ALIAS_CONFLICT` |
| 같은 Component 자연키 또는 같은 대상 Alias의 상충 내용 | 활성 후보 차단 | `MEMBER_CONFLICT` |

실패 상세에는 Source 원문이나 Alias 문자열을 기록하지 않고 안정적으로 생성된 구성원 참조만 기록한다.

## 최초 v1 결정적 산출물 (과거 기록)

- Schema version: `medication-catalog-v1`
- Manifest: `docs/validation/rag/catalog/synthetic-catalog-v1/manifest.json`
- Canonical JSONL: `docs/validation/rag/catalog/synthetic-catalog-v1/catalog.jsonl`
- JSON은 UTF-8, key 정렬, compact separator, LF 종료 규칙을 사용한다.
- manifest hash는 자기 자신을 제외한 manifest 의미 값의 canonical bytes로 계산한다.
- export checksum은 `catalog.jsonl` 전체 bytes의 SHA-256이다.

## 저장 경계

`CatalogBuildRepository.save_build()`는 Catalog 구성원과 manifest candidate를 호출자가 소유한 하나의
transaction에 저장하는 포트다. 검증 성공 후 이 메서드가 한 번만 호출되며, 저장 예외는 성공 결과로
변환하지 않는다.

현재 #164 DB 기반에는 다음 필드와 관계가 없어 손실 없는 Worker DB adapter를 구현할 수 없다.

- Catalog build/version과 manifest candidate 저장 구조
- Catalog build와 여러 Source Snapshot/version의 lineage
- Candidate용 Search Entry 저장 구조
- Alias의 `alias_source`, `review_status`, `status`, `is_effective`
- Alias Source Snapshot과 대상 Product/Ingredient Snapshot의 분리

따라서 실제 PostgreSQL 저장은 `BLOCKED_BY_WORKER_DB_ADAPTER`로 유지한다. 위 구조의 DB 계약과
migration이 승인되면 `CatalogBuildRepository` 구현체와 PostgreSQL commit/rollback 통합 테스트를 연결한다.
현재 구현은 Backend ORM model을 Worker에서 import하지 않는다.

## 실행 결과

```text
uv run pytest ai_worker/tests/rag/catalog -q
47 passed

uv run pytest ai_worker/tests/rag -q
711 passed

uv run ruff check ai_worker/tasks/rag/catalog ai_worker/tests/rag/catalog
All checks passed

MYPYPATH="$PWD/backend:$PWD" uv run mypy ai_worker/tasks/rag/catalog
Success: no issues found in 7 source files
```

## 남은 통합 조건

- #323 병합 후 승인된 Worker DB adapter 기준으로 branch를 갱신한다.
- 위 DB 계약과 migration을 DB 담당자에게 확인한다.
- 실제 PostgreSQL에서 성공 commit과 주입된 실패 rollback 시 부분 Catalog/manifest가 0건인지 검증한다.
- Source/Catalog approval과 Candidate Index·Resolver 평가 전 Runtime activation은 수행하지 않는다.

저장소 전체 테스트에서는 DB 비의존 테스트 `3179 passed, 10 skipped`까지 진행됐고, PostgreSQL migration
17건은 호스트 실행 환경에서 Compose 내부 hostname `postgres`를 해석하지 못해 실패했다. 로컬 DB를
대상으로 한 migration 재실행은 downgrade/upgrade와 trigger 변경이 기존 데이터를 바꿀 수 있어 수행하지
않으며, 격리된 CI PostgreSQL에서 확인한다.

## 2026-09-08 계약 결정과 독립적인 계산 계층 보완

PR #329의 `0740402eedd5b07188d4a123cc15108f40171c02`를 기준으로 다음 구현만 보완했다.

- 완전히 같은 구성원 행의 중복 검사를 리스트 순회에서 행 전체를 키로 하는 사전 조회로 변경했다.
  최초 등장 순서와 상충 행을 보존한다. Identity 또는 reference만으로 행을 합치지 않는다.
  Product에서 만들어진 기존 Entry 목록도 보존하고, Alias Entry 추가 시 중복 조회만 집합을 사용한다.
- `target_source_snapshot_id`는 `None`일 때만 Alias 자체 Snapshot으로 기본 설정한다.
  명시적인 빈 문자열·공백은 기존 필수 문자열 검증으로 거부한다. 유효한 교차 Snapshot 참조는 유지한다.
- 최초 등장 순서, 동일 reference의 상충 행, 서로 다른 원본 키, 기본값과 빈 입력에 대한 회귀 테스트를 추가했다.

합성 입력으로 기존 코드와 수정 코드의 구성원 전체를 비교했다. 기존 fixture와 서로 다른 제품
1,000개/5,000개를 각각 두 번 입력한 경우 결과가 같았다. 한 로컬 실행에서 2,000개 입력은
약 0.090초 → 0.023초, 10,000개 입력은 약 1.884초 → 0.118초였다. 측정은 제품 생성 이후
`build_catalog_members` 호출 시간을 비교한 참고값이며 성능 보장이나 운영 부하 검증이 아니다.

검증 명령은 기존 Python 3.13 테스트 환경에서 실행했다.

```text
python -m pytest ai_worker/tests/rag/catalog -q
54 passed

python -m pytest ai_worker/tests/core ai_worker/tests/ocr ai_worker/tests/rag ai_worker/tests/evaluation -q
2004 passed, 8 skipped

ruff check .
All checks passed

ruff format . --check
509 files already formatted

MYPYPATH="$PWD/backend:$PWD" python -m mypy backend/app ai_worker
Success: no issues found in 435 source files
```

기존 export golden hash 테스트도 통과했다. DB·migration·외부 Provider 변경은 없으며,
PostgreSQL/Redis를 포함한 전체 CI runner는 이번 로컬 검증에 포함하지 않았다.
승인 상태/receipt·Ingredient 입력·의미상 Alias dedupe·service 실패 응답에 관한 기존 리뷰와
#164/#165/#166 공유 계약 결정은 별도 후속 범위로 남아 있다. 이 보완은 해당 리뷰의 해결이나
Runtime 활성화 승인을 의미하지 않는다.

## #319 반영 후 CI multiple heads 해결

#329에 Evaluation revision과 기존 Source Artifact revision이 별도 head로 남아
`alembic upgrade head`가 실패했다. #323의 검증된 최신 커밋 `55e2aff`를 병합하여
Evaluation → Source Artifact → Receipt/FAILED 재시도 → Verification 보호 순서를 반영했다.
단일 head는 `165d7e6f5041`이며, #323의 Source 리뷰 보완도 함께 상속했다.

- 격리 PostgreSQL 17 빈 DB에서 `upgrade head` 성공
- 전체 migration 테스트: **66 passed**
- Catalog·Source ingestion·Governance Receipt 테스트: **334 passed**
- PostgreSQL Source lifecycle·Evaluation repository 테스트: **16 passed**
- 전체 Ruff·서식 검사 통과 (**518 files**), Mypy **441개 소스 파일 통과**
- 이 수정은 Catalog 승인 receipt·Ingredient 입력·Alias 의미상 dedupe 등 기존 #329 리뷰의 해결을 의미하지 않는다.

## #329 RAG 변경 요청 반영 v2

- 독립 Ingredient registry를 입력으로 추가하고 Component의 암묵적 성분 생성을 제거했다. 기존 복합제 fixture 전체(Component 3개·Ingredient 2개)가 서비스 validate/export를 통과하며, 누락 Ingredient는 저장 없이 REJECTED다.
- 같은 제품의 반복 Alias는 provenance 행을 보존하고 검색 항목만 결정적으로 선택한다. 같은/다른 Snapshot 반복·입력 순서 반전과 다른 제품의 Alias 충돌을 각각 검증한다.
- Product/Ingredient/Alias 대상 누락 mapping 오류는 REFERENTIAL_INTEGRITY_INVALID로 서비스 REJECTED 처리한다.
- 승인 verifier가 없거나 Source 미승인·STALE이면 NOT_APPROVED이며 Candidate Index가 거부한다. 합성 승인 경로, 잘못된 checksum/Source receipt 거부, gate 변조 및 manifest exact recomputation을 검증한다.
- schema와 manifest는 v2로 갱신했다. 기존 합성 manifest의 APPROVED/CURRENT 자동 부여를 제거하고 새 golden으로 바꿨다. 파일 바이트 checksum과 승인 envelope hash는 별개다.
- 실제 승인 adapter와 정본 projection hash 최종 대응·DB provenance는 후속 범위다. 상세 계약과 결정 기록은 Catalog build v2 문서를 따른다.

## 2026-09-08 리뷰 보완 및 develop 병합 검증

리뷰 보완 커밋 `b8fa5e8` 이후 develop `1adf081`의 #328(Evaluation publish 파일 소유권 보호), #331(Frontend 재접속 복원), #334(Snapshot 최소 구현 deviation 문서)를 반영했다. 세 PR은 migration을 추가하지 않았으며 기존 Source migration을 보존했다. #334 문서 병합은 #164의 정규 normalization/provenance DB 구현 완료를 의미하지 않는다.

- Catalog 단위·서비스·Candidate 인계 회귀: **68 passed**
- 병합 후 Worker core/OCR/RAG/Evaluation 및 Source governance receipt: **2,152 passed, 8 skipped**
- PostgreSQL 17 빈 DB에서 `alembic upgrade head` 성공; 단일 head **165d7e6f5041**
- PostgreSQL 전체 migration·rollback 회귀: **66 passed**
- PostgreSQL Source lifecycle·Evaluation repository 통합: **16 passed**
- Ruff 및 서식: **520 files 통과**, Mypy: **443 source files 통과**
- Frontend(Node 24.19.0, 테스트용 VITE_API_BASE_URL 설정): **259 passed**, build·lint 통과
- `git diff --check` 및 병합 staged diff 검사 통과

Frontend 최초 실행은 테스트 API URL 미설정으로 실패했고, fixture가 요구하는 localhost URL을 설정한 재실행에서 전체 통과했다. 사용자 DB 대신 임시 PostgreSQL 컨테이너만 사용했다. Redis를 포함한 전체 CI runner는 로컬에서 실행하지 않았으며 원격 CI 결과는 별도로 확인해야 한다.

## #324 및 #323 DB-owned transition 반영

#323의 `aef61f7`을 병합했다. #324 Prescription Version revision 뒤에 Source chain이 이어지며, Source 상태의 Runtime raw UPDATE 우회는 DB-owned transition 함수로 차단된다. 두 PR에 별도 migration을 생성하지 않고 같은 이력을 공유한다. #329 계산·export 계약 변경은 없다.

- #323과 migration·migration test 파일 동일함을 확인
- 병합된 #329에서 전체 PostgreSQL migration·rollback: 83 passed
- Frontend 259 passed, build·lint 통과
- 단일 head: `165e8f706152`

#324의 처방 Version DB 기반이나 이번 Source 상태 보호가 #164의 normalization/provenance 정렬 완료를 의미하지는 않는다. #166 DB 후속 범위와 실제 Runtime 비활성 상태를 유지한다.

병합 후 Worker·Source receipt 2,152 passed, 8 skipped; Ruff·format(523 files), Mypy(443 source files) 및 diff 검사 통과.

## 2026-09-08 현우님 추가 리뷰: Candidate 소비 경계·P0 allowlist

기준: `4d1fc38` 이후 추가 보완. 아래 결과는 이 절의 변경을 포함한 로컬 working tree에서 실행했으며, 앞선 SHA의 검증 결과를 최신 결과로 바꿔 쓰지 않는다.

- Candidate public build는 전체 CatalogExportArtifacts를 받아 manifest/hash·JSONL·typed gate/구성원/count 결속을 검증한다. raw typed 입력과 승인 상태만 바꾼 입력은 CATALOG_MANIFEST_INVALID로 거부하며 embedding을 호출하지 않는다.
- P0 Product MFDS_ITEM_SEQ / Ingredient MFDS_INGREDIENT_CODE allowlist를 build와 Candidate 입력에 적용한다. EDI·NHIS·HIRA·미등록 체계는 lookup 전에 제외한다.
- 기존 Candidate 구성원 계산 테스트는 내부 순수 함수의 검증으로 구분하고, public API에는 정상 승인 artifact·미승인·raw 입력·gate/구성원/manifest/JSONL/checksum 변조 회귀를 추가했다.
- Catalog·Candidate 집중 회귀: 180 passed.
- RAG 전체 및 Source governance receipt: 853 passed.
- Ruff·format, RAG Mypy(35 source files), git diff --check 통과.
- DB·migration 변경 없음. 현재 Alembic head는 165e8f706152이며, PostgreSQL 및 전체 서비스 CI는 이번 보완에서 재실행하지 않았다.
- #323이 미병합이므로 Source 중복 diff 정리는 미완료다. #323 병합 후 develop 반영·단일 head·diff·최종 HEAD CI를 재검증한다. 이 수정 자체로 #166 DB 통합·실제 승인 adapter·Runtime 활성화를 완료하지 않는다.

## #323 병합 후 develop 정렬 및 최종 로컬 검증

#323 squash merge가 포함된 develop `2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd`를 병합했다. Source 문서·Decision·Receipt·migration 테스트의 충돌 5개는 병합된 develop 버전을 반영했다. Source 코드·migration·role 설정은 develop과 동일하며, PR diff는 Catalog·Candidate 관련 28개 파일만 남는다. 앞 절의 '#323 미병합' 상태는 이 병합으로 해소됐다.

- 현우님 리뷰 수정 `b3b741b`의 artifact 소비 검증·P0 allowlist 유지.
- 신규 Source CHECK revision까지 반영한 Alembic 단일 head: `165f90716263`.
- Worker 전체: **2,167 passed, 8 skipped**.
- PostgreSQL 16 migration·rollback·Source lifecycle·Source governance receipt: **110 passed**.
- 전체 Ruff·format: **526 files 통과**, Mypy: **445 source files 통과**.
- 추가 회귀 테스트의 혼합 입력 변수 타입 주석을 보완했다. 동작 변경 없이 전체 Mypy를 통과하도록 정렬했다.
- `git diff --check` 및 Source 중복 diff 없음 확인.

첫 DB 검증은 빈 DB에 `upgrade head` 초기화를 생략해 기존 Profile downgrade 테스트 1건이 실패했다. CI 순서대로 head 초기화 후 같은 범위 전체 110건이 통과했다. 사용자/운영 DB는 사용하지 않았다. Catalog PostgreSQL adapter는 여전히 후속 범위이며 이번 DB 결과가 해당 adapter 검증을 의미하지 않는다. 원격 CI는 이 병합 커밋을 push한 후 확인한다.

## 2026-09-08 최신 리뷰: 원문 NFC 경계·v2 인계 문서 정렬

검증 기준: PR head `63f2786e011a538633190c7dea6f5cc824dd6c32`에 develop
`bd6b4d6bc2d2a04d24bd88012dbaea91ee1aa3fb`를 병합한 `57a2401` 및 이 절과 함께 커밋하는 수정 working tree.
#340·#342를 포함한 develop 병합은 충돌 없이 완료했다. 앞선 절의 수치는 각 당시 검증 기록이다.

- 원문 제품명·성분명·별칭·제품 표시 속성·Search Entry 표시 문자열은 NFD도 보존한다.
- normalized 필드·Identity·참조·설정은 기존 NFC 검증을 유지한다.
- Candidate hash의 전체 NFC 재작성도 제거했다. 동일한 Catalog envelope 아래에서도 표시 원문이
  다르면 member hash가 구분되는 회귀를 추가했다. 기존 NFC 입력의 직렬화 결과는 변하지 않는다.
- 서비스 build → 합성 승인 receipt → manifest/JSONL 검증 → 공개 Candidate Index 성공 경로에서
  NFD 원문 보존과 normalized NFC를 확인했다. 합성 receipt는 실제 운영 승인이 아니다.
- #167 설계·구현 계획과 이 문서의 상단을 `CatalogExportArtifacts` / `medication-catalog-v2`로
  정렬했다. v1 산출물·초기 테스트 수치는 과거 기록으로 명시했다.

실행 환경: 기존 Python 3.13 테스트 환경. Worker에는 `PYTHONPATH=.`를 사용했다.

```text
python -m pytest ai_worker/tests/core ai_worker/tests/ocr ai_worker/tests/rag ai_worker/tests/evaluation -q
2167 passed, 8 skipped

ruff check .
All checks passed!
ruff format . --check
527 files already formatted
MYPYPATH=backend:. python -m mypy backend/app ai_worker
Success: no issues found in 445 source files

python -m alembic -c backend/alembic.ini heads
165f90716263 (head)
git diff --check
통과
```

이번 수정에는 DB·migration 변경이 없다. Alembic 확인은 revision graph 검사이며 PostgreSQL
upgrade/rollback 실행 검증이 아니다. 전체 Backend·PostgreSQL·Redis 통합 및 GitHub CI는 이
수정의 push 후 최신 HEAD에서 확인해야 한다. #166 DB adapter·Runtime 활성화는 후속 범위다.

## #166 DB 후속 1~3단계 검증 (2026-09-08)

기준: develop `e20acb9`, v2 hash 보강 `59684d6`, 저장 준비 구현 `741795c`.
현재 공개 인계는 계속 `CatalogExportArtifacts` / `medication-catalog-v2`다.
과거 v1 기록의 숫자와 경로를 이번 검증 증빙으로 재사용하지 않는다.

- 고정 합성 bytes·digest: `tests/fixtures/rag/catalog/hash-v2/`.
- hash 보강 22건, DB 비의존 저장 준비 26건 추가.
- RAG 전체 `963 passed`; Ruff 통과; 변경 Python format 통과; RAG Mypy 37파일 통과.
- 단일 Alembic head `169b2c3d4e5f`, revision graph 29개 확인. 새 migration 없음.
- 저장 준비 함수는 SQL을 실행하지 않는다. 실제 PostgreSQL 적재·commit/rollback·실행 FK·승인
  adapter는 미완료이며 테스트 결과를 DB 통합 완료로 해석하지 않는다.

4단계는 D-02 인계 및 합의된 선행 migration 병합 대기다.
[상세 인계 점검](../designs/jye-rookie/issue-166-db-migration-readiness.md)을 따른다.

## #166 DB 후속 5단계 선행 복원 검증 (2026-09-08)

3단계 `741795c`의 저장 준비 자료를 v2 전체 artifacts로 복원하는 독립 코드를 추가했다.
고정 합성 bytes 복원 → manifest/JSONL 검증 → 공개 Candidate 성공을 확인했고, 부적격·손상된
자료는 거부했다. 신규 복원 테스트 35건, RAG 전체 `998 passed`, Ruff·format·Mypy 38파일 통과.
기존 v2 bytes·digest와 D-02 미확정 기준은 유지한다.

이는 메모리와 합성 bytes를 이용한 복원 검증이다. 실제 PostgreSQL adapter·commit/rollback·재시도
통합 테스트는 아직 수행하지 않았고, 4단계 인계 전 5단계 전체 완료로 표시하지 않는다.


## #166 후속 5단계 재개 — 현재 승인 대조

`test_restore_current.py`는 복원된 과거 승인과 현재 verifier 응답을 분리해 검증한다.
정상/역순 Source receipt는 고정 v2 bytes 그대로 공개 Candidate 인계를 통과한다.
포트 없음, 거부 receipt, 다른 버전/checksum/receipt, Source subset·중복·미승인·STALE·불완전은
반환을 차단한다. 같은 자료를 다시 읽어도 verifier를 다시 호출하며 첫 성공 뒤 철회를 캐시하지 않는다.
손상·미승인 저장 자료는 승인 서비스 호출 전 차단하고, 서비스 예외 원문 비노출·취소 전파도 검사한다.

실제 승인 DB·시간에 따른 만료 판단·PostgreSQL 왕복·동시 철회 잠금의 통합 증빙은 아니다.
D-02는 미확정이며 run/FK를 구현하지 않았다. 실제 DB 통합 완료로 기록하지 않는다.

검증 결과: 신규 승인 재검사 18건 포함 RAG 전체 **1,016 passed**. RAG Ruff 통과, 변경 Python
format 통과, RAG Mypy **38 source files** 통과, `git diff --check` 통과.
실행: `PYTHONPATH=backend:. python -m pytest ai_worker/tests/rag -q`,
`MYPYPATH=backend:. python -m mypy ai_worker/tasks/rag`. PostgreSQL·실제 승인 저장소는 실행하지 않았다.
