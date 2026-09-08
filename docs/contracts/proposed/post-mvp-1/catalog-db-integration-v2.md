# #166 Catalog DB 적재·저장 연결안

- 상태: **Proposed**. 3단계의 내부 저장 준비 코드는 구현했으나 DB schema·migration은 미확정이다.
- 구현 담당: 김지혜. Candidate·의미 계약 검토: 정현우. DB·FK·transaction 검토: 송은영.
- 현재 적용 기준: [기존 v2 계약](../../targets/post-mvp-1/catalog-build-v2.md).
- 최신 협의 기준: 김지혜가 제공한 D-05 v2 검토 반영본. 원본 공유 문서 자체를 복제하지 않는다.
- Source 인계 기준: [Source 계약](../../targets/post-mvp-1/rag-source-ingestion-v1.md).

## 구현 상태

`ai_worker/tasks/rag/catalog/storage.py::prepare_catalog_storage`는 검증된 v2 구성원과
artifacts로부터 SQL 실행 전 사용할 불변 저장 자료를 만든다. Backend model을 import하지 않고
DB·파일·네트워크에 쓰지 않는다. 기존 `save_build(members, artifacts)` 서명과 Candidate v2 인계를
변경하지 않는다. PostgreSQL adapter가 준비되면 adapter 경계에서 호출하도록 연결할 코드다.
현재 서비스의 repository를 이 함수나 가짜 저장소로 대체하지 않는다.

| 자료 | 구현한 의미 | 아직 보장하지 않는 것 |
| --- | --- | --- |
| identities | PRODUCT/INGREDIENT의 공식 type·code system·code, 이름 기반 병합 없음 | DB upsert·경합 제어 |
| source_refs | v2 Snapshot ID·Source version 쌍 전체, 직접 구성원이 없는 승인 Source도 보존 | 실제 Snapshot 조회·검증 상태·normalization 실행 FK |
| rows | v2 종류·참조·원문 포함 canonical record bytes·출처와 구성원 간 참조 | 실제 Publication PK, 정본 Set/member, READY 전이 |
| hash 자료 | 종류·schema·현재 계약 spec·값·계산 bytes·대상 구분 | DB 저장 컬럼명·유일성·FK, 새 projection/Runtime hash |
| manifest_json | 받은 v2 manifest bytes 보존 | 실제 승인 권한·서명·회수·만료 확인 |

내부 hash 자료의 `contract_spec_version`은 JSONL 규칙을 포함한 현행 v2 계약을 가리킨다.
JSONL만을 위한 새 canonicalization spec을 임의로 발급하지 않는다. 물리 저장 시의
`canonicalization_spec_version` 표현·대상 참조와 매핑은 D-05 DB 리뷰에서 정렬한다.
export checksum과 envelope hash를 서로 다른 kind/target으로 보존한다.

저장 준비에서 수행하는 검사:

- 전달한 members와 artifacts가 같은 v2 canonical JSONL을 나타내는지 확인한다.
- 중복 공식 Identity·고아 Component·상충 Alias·상충 구성원을 다시 검증한다.
- Alias는 안정 Identity를 가리키고 Search Entry는 선택된 Product와 nullable Alias 참조를 가진다.
- Product 이름 Entry는 Product 출처, Alias Entry는 Alias 출처를 보존한다.
- 현재 builder와 같은 적격성·대표 Alias 선택으로 Search Entry 집합을 재구성해 비교한다.
- Product/Alias의 교차 Snapshot 허용을 Component에 확대하지 않는다.
- 구성원 수·schema·normalization version을 확인하고 hash 대상 bytes를 재계산한다.
- 오류에는 입력 원문 대신 고정 메시지만 사용한다. 저장 준비 성공으로 승인·현재성·READY를 부여하지 않는다.

`member_ref`는 기존 v2에서의 구성원 참조다. 같은 Snapshot을 다른 내용으로 처리해도 같은 ref가
나올 수 있으므로 DB의 단독 Publication key나 normalization 실행 ID로 사용하지 않는다.
이번 저장 자료는 차이가 나는 bytes를 그대로 보존할 뿐, 그 두 실행의 영속 식별을 결정하지 않는다.

## D-03: Identity·Alias·Set 제안

| 항목 | 제안 | 결정·검증 접점 |
| --- | --- | --- |
| 안정 Identity | type·code system·canonical code를 자연 식별자로 upsert. DB UUID는 물리 FK로만 사용 | 동일 공식 코드 재등장·동시 적재에서도 중복 Identity 없음 |
| Product/Ingredient | 기존 테이블 확장. 원문·정규화값·상태·Source record key 보존. Snapshot별 행과 Identity 분리 | Publication/실행 key는 D-02 인계 후 결정 |
| Ingredient 이름 unique | 공식 코드가 다른 동명 성분을 막는 제약은 Identity·Publication 기준으로 전환 제안 | 코드 없는 기존 행 자동 코드 생성 금지. 기존 데이터 조사와 이행안 필요 |
| Alias 대상 | 대상 Snapshot 행 FK를 안정 Identity FK로 전환 제안 | 대상 type 일치, Alias 자체 출처와 대상 구성원 출처 분리 |
| Alias 상태 | 현재 v2 alias_source·review_status·status·is_effective를 손실 없이 보존 | boolean 하나로 축소 금지. expiry/승인 회수 표현은 추가 검토 |
| 기존 is_approved 이행 | true만 보고 새 APPROVED 상태·승인 receipt를 만들지 않음. 출처·상태·승인 근거를 검토해 이행 | 근거 없는 기존 행은 적격 검색 입력으로 공개하지 않음. 누락값을 임의 backfill하지 않음 |
| Alias 집합 | 하나의 입력 Catalog에 포함된 Alias 구성원 전체와 선택된 Search Entry를 구분해 보존하는 안 | 부적격·성분 Alias는 보존하되 제품 검색 승격 없음. 전체 관찰 집합과 정본 Alias Set의 범위 동일성은 리뷰 필요 |
| Crosswalk | 현재 typed input에 승인 Identifier/Crosswalk 집합이 없어 자동 생성하지 않음 | 빈 READY Set으로 위장하지 않음. P0 포함 범위 결정 후 별도 구현 |
| 불변 Set/member | 승인된 Set 범위와 실행/Publication key를 연결해 선택 구성원을 고정 | 새 catalog_build 테이블을 전제하지 않음. READY 이후 수정 대신 새 버전 |

현재 저장 준비의 `rows` 목록은 정본 Set을 대신하지 않는다. Set 포함 범위·상태·빈 집합 규칙을
확정하기 전에는 DB Set 상태나 READY flag를 코드에 만들어 넣지 않는다.

## D-04: Component와 Loader 근거

확인한 구현:

- `source_ingestion/parse.py`는 검증된 제품 수집 레코드와 ITEM_SEQ 기반 checksum을 제공한다.
- Catalog의 Component 입력은 공식 제품·성분 코드, Snapshot, role, order, strength, release profile이다.
- `tests/fixtures/rag/catalog/synthetic_components.json`은 합성 3행이며 ACTIVE_INGREDIENT만 있다.
  실제 Source에서 role이 단일하다는 증거로 사용할 수 없다.
- 현재 builder는 `(product_ref, ingredient_ref, component_role)`로 참조를 만들며 같은 Snapshot의
  독립 Ingredient registry를 조회한다. 성분을 Component 이름에서 임의 생성하지 않는다.
- 기존 DB `Numeric(12,4)`와 v2의 strength 문자열은 같지 않다. `010.00`, `5.0`, `2.50` 등을
  숫자로 변환하면 원문/직렬화 재현이 달라질 수 있으므로 저장 준비에서 그대로 보존한다.

제안:

1. 실제 Source 근거 전에는 자연키에서 role을 제거해 강화하지 않는다.
2. source_record_key가 입력에 없다고 구성원 hash를 원본 레코드 키로 넣지 않는다.
   실제 원본 키와 row별 재적재 필요성을 확인한 뒤 입력·DB 전환을 함께 제안한다.
3. 문자열 함량·release profile·원문 재현에 필요한 필드는 기존 숫자 컬럼만으로 축소 저장하지 않는다.
4. 후속 Loader는 명시적인 FK 값과 INSERT를 사용하는 방향으로 제안한다. relationship 대입을
   사용하지 않는다면 viewonly/overlaps만 정리하는 변경은 포함하지 않는다. 실제 adapter 구현 때 확정한다.

## D-06: transaction·실패·재시도 제안

현재 단계는 아래 실행 순서의 설계만 제공한다. DB 실패 테스트나 실제 commit을 완료한 것은 아니다.

1. DB 밖에서 입력 mapping·구성원·v2 artifacts 검증과 저장 준비를 수행한다.
2. DB transaction을 열고 확정된 요청/실행 key로 경합을 제어한다. key는 D-02와 함께 확정한다.
   envelope hash 단독으로 실행의 멱등키를 만들지 않는다.
3. 해당 transaction에서 Source·Publication 참조 및 승인 근거의 현재 적격성을 재확인한다.
   긴 외부 호출을 DB lock 안에 넣지 않고, 검증된 receipt의 저장 버전/회수 상태 결속을 확인하는 안을 제안한다.
4. 안정 Identity upsert → Product/Ingredient 구성원 → Alias/Component → Search Entry →
   확정된 Set/member와 manifest 참조 순으로 적재하고 모든 참조를 검증한다.
5. 구성원·Set·manifest 참조를 한 transaction에서 commit한다. READY 공개는 별도의 계약 조건이
   충족된 경우에만 적용한다. NOT_APPROVED/STALE 자료 저장으로 Candidate를 활성화하지 않는다.
6. 실패 시 전체 rollback하고 원문 없는 실패 분류·요청/실행 식별을 별도 감사 경계에 기록한다.
   감사 실패까지 성공으로 바꾸지 않는다. 저장 예외를 Catalog REJECTED로 임의 변환하지 않는다.
7. 재시도 시 같은 확정 key의 완전한 결과와 payload를 확인해 재현한다. 다른 내용은 기존 행을
   덮어쓰지 않는다. commit 결과가 불명확하면 재조회로 결과를 판별하기 전 중복 성공을 보고하지 않는다.

파일 산출물은 DB commit 전에 공개하지 않는다. 내부 임시 파일을 먼저 만들 경우 commit 실패 뒤
잔여 파일의 식별·정리 기준을 별도로 명시한다. #347의 Source Artifact 정리 구현이 Catalog 파일을
자동으로 정리한다고 간주하지 않는다. 실제 파일 저장을 adapter에 연결할 때 범위를 검토한다.

## 4단계 migration 착수 조건

D-02 미확정 유지, 별도 run 임의 생성 금지, ingestion run으로 정본 normalization_run_id 대체 금지가
현재 합의다. 따라서 아래 인계 전에는 신규 Alembic revision을 생성하거나 기존 Catalog FK를 바꾸지 않는다.

- #164·#165의 실행/Publication 식별·컬럼·FK·재처리/재시도 관계 인계
- D-03 Set 범위·상태 및 기존 Alias 이행, D-04 실제 Source 근거, D-05 저장 접점, D-06 transaction 정렬
- 은영님 Evidence/Citation 작업 병합과 최신 migration head 확인

이 문서 승인만으로 위 조건이 충족되는 것은 아니다. 인계 후 실제 구현·migration·통합 테스트 및
지정 리뷰어 검토를 같은 후속 PR 흐름에서 완료하고 상태를 갱신한다.

## 5단계 선행 가능 범위: 저장 자료 복원

`ai_worker/tasks/rag/catalog/restore.py::restore_catalog_storage`는 내부 저장 자료를 검증하여
현재 공개 입력인 `CatalogExportArtifacts`를 반환한다. 실제 DB 조회·transaction adapter는 아직
구현하지 않았다. D-02와 4단계 migration 없이 가능한 복원·검증 코드만 먼저 준비했다.

- canonical record를 현재 dataclass 타입으로 엄격하게 복원한다. 알 수 없는 key, 누락 필드,
  잘못된 enum·타입을 무시하거나 기본값·자동 형변환으로 보정하지 않는다.
- 중복 JSON key를 최상위·중첩 객체에서 거부한다.
- 현재 producer로 전체 manifest를 재생성해 구성원 수·Source·receipt·gate 결속을 확인한다.
- 3단계 저장 준비 함수를 다시 거쳐 Identity·출처·구성원 링크·hash 종류/spec·계산 bytes까지 비교한다.
- DB 조회 시 행·Identity·Source·hash 목록 순서는 달라도 허용하지만 중복이나 누락은 허용하지 않는다.
- 반환값은 typed Catalog 단독이 아닌 manifest·JSONL을 포함한 v2 전체 artifacts다.
- 승인 receipt는 당시 기록이다. 복원은 승인 주체·현재 회수·만료·DB 실행 FK 검증을 대신하지 않는다.
  실제 adapter는 소비 전 현재 적격성을 별도로 검증해야 하며, 합성 receipt를 운영 승인으로 쓰지 않는다.

합성 bytes를 사용하는 복원 검증과 실제 PostgreSQL commit/rollback·재시도 검증은 별개다.
후자는 4단계 완료 후 5단계의 남은 범위로 수행한다. 임시 테이블이나 별도 run을 만들지 않는다.


## 5단계 재개 — 복원 뒤 현재 승인 재검사

`restore_current_catalog_storage`는 기존 복원 검증을 통과한 자료에 대해
`CatalogApprovalVerifier`를 매번 호출하고, 현재 결과가 저장 당시 v2 approval payload와
동일하게 결속되는 경우에만 원래 `CatalogExportArtifacts`를 반환한다.

- 검증 포트 없음·receipt 없음·회수/만료 등으로 부적격한 결과·Source 범위/상태 불일치는 반환을 차단한다.
- 합성 테스트에서 verifier의 거부 결과를 주입한다. 실제 회수/만료·권한 조회 구현은 아직 연결하지 않았다.
- Source receipt 순서 차이는 현재 producer 규칙으로 정규화하지만, 새 receipt ID로 기존 manifest와
  hash를 조용히 바꾸지 않는다. 새 승인 근거의 재발행 절차는 별도 adapter 설계·리뷰 대상이다.
- 이미 저장된 NOT_APPROVED/STALE 자료를 이 함수에서 승인 상태로 승격하지 않는다.
- 승인 서비스 실패는 원문 없는 내부 복원 오류로 처리하며 비동기 취소는 전파한다.
- 이는 호출 시점의 승인 대조다. DB 잠금·원자 저장·이후 소비까지의 동시 철회 방지를 보장하지 않는다.

D-02는 미확정 그대로다. 이 작업은 run 생성·ingestion run 대체·FK·migration을 추가하지 않는다.
기존 public Candidate v2 입력·hash 의미도 유지한다. 실제 DB adapter·transaction 통합은 보류 상태다.
