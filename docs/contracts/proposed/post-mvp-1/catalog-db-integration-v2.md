# #166 Catalog DB 적재·저장 연결안

- 상태: **Proposed / DB 인계·결정 항목 검토안 작성 완료**. 승인·실제 DB 통합 완료가 아니다. D-02는 미확정이다.
- 구현 담당: 김지혜. Candidate·의미 계약 검토: 정현우. DB·FK·transaction 검토: 송은영.
- 현재 적용 기준: [기존 v2 계약](../../targets/post-mvp-1/catalog-build-v2.md).
- 최신 협의 기준: 김지혜가 제공한 D-05 v2 검토 반영본. 원본 공유 문서 자체를 복제하지 않는다.
- Source 인계 기준: [Source 계약](../../targets/post-mvp-1/rag-source-ingestion-v1.md).

- 검토용 인계 목록과 답변 양식: [DB 인계·결정 검토표](../../../designs/jye-rookie/issue-166-db-handoff-review.md).

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

## D-02: 실행 참조 인계 요청 — 미확정

**결정 상태는 미확정이다.** 아래는 김지혜가 #165 실행 정책과 #166 소비 요구로 제안할 논리 조건이다.
새 run 테이블 생성, 기존 ingestion FK 채택, 실행 key 형식 확정 또는 schema 승인으로 해석하지 않는다.

- 기존 Snapshot을 다른 Catalog normalization/build 버전으로 처리한 결과는 별도 실행 결과로 구분해야 한다.
- 같은 실행 요청의 장애 재시도와 새 버전 재처리를 구분한다. 전자는 시도 이력을 남기되 같은 요청 결과를
  중복 발행하지 않고, 후자는 과거 결과를 덮어쓰지 않는다.
- 복수 Source Snapshot을 입력으로 쓰는 Catalog에서는 실행이 사용한 Snapshot 집합과 행별 출처를
  모두 재현해야 한다. Snapshot 하나당 실행 하나라고 가정하지 않는다.
- 실행 성공과 구성원 발행의 관계, FAILED 실행의 결과 참조 유무, NO_CHANGE 재검증 기록의 위치를
  별도로 명시한다. #165 Source 충돌·FAILED/NO_CHANGE 규칙은 이 제안으로 변경하지 않는다.
- 실행 ID는 실제 실행을 식별한다. `catalog_version`, `member_ref`, `normalization_version`, envelope hash
  또는 ingestion run ID를 정본 실행 ID로 대체하지 않는다.

김지혜는 위 시나리오의 생성·완료·재시도 정책을 제안한다. 송은영은 적용 가능한 저장 구조·PK/FK와
기존 구조 전환을 검토하고, 정현우는 정본 normalization 실행과 Catalog 재처리의 의미를 검토한다.
한 사람의 이슈 종료나 반응만으로 D-02 상태를 바꾸지 않는다. 최종 인계에는 적용 문서·결정 근거,
물리 참조 키와 생성 인터페이스, 구현 담당 및 revision을 기록한다.

## D-03: Identity·Alias·Set 변경안 — 리뷰 요청

아래 논리 구조를 권장한다. 물리 테이블명과 실행/Publication key는 D-02 및 DB 리뷰 전 미확정이다.

| 대상 | 구체 변경안 | DB/adapter 검증 |
| --- | --- | --- |
| Identity | `(entity_type, code_system, canonical_code)` 유일. P0 Product는 MFDS_ITEM_SEQ, Ingredient는 MFDS_INGREDIENT_CODE. 이름 기반 병합 금지 | 동시 upsert의 동일 Identity 1행. 다른 코드·같은 이름은 별도 Identity. 기존 비허용 체계의 임의 치환 금지 |
| Product/Ingredient | 기존 테이블 확장. Identity, 출처 Snapshot, 인계된 실행/Publication 참조와 현재 v2 필드 보존 | 같은 실행의 상충 구성원 거부. 새 실행은 과거 구성원을 덮어쓰지 않음. 정확한 unique 조합은 실행 키 인계 후 지정 |
| Alias 관찰 행 | 안정 Identity를 대상 FK로 사용. 원문·정규화값·alias_source·review_status·status·is_effective 및 Alias 자체 출처 보존 | Product/Alias 교차 Snapshot 허용. Source가 다른 동일 Alias 관찰을 삭제하지 않음 |
| Alias 선택 집합 | 해당 Catalog에서 선택한 Alias 관찰 행 전체를 불변 member로 보존하는 안 | PENDING·REJECTED·INACTIVE·Ingredient Alias도 관찰 이력으로 보존. 이 집합을 승인된 검색 집합과 동일시하지 않음 |
| 검색용 선택 | 승인·활성·유효 Product Alias만 현재 builder 규칙으로 Search Entry 선택 | 같은 Product·동일 정규화 문자열의 복수 출처는 모두 보존하고 대표 Alias만 결정적으로 선택. 서로 다른 활성 Product 충돌 거부 |
| Search Entry | PRODUCT_NAME은 Alias 참조 NULL, APPROVED_ALIAS는 필수. 선택 Product·Alias와 같은 Catalog 구성에 결속 | Product 이름 출처는 Product Snapshot, Alias형 출처는 Alias Snapshot. 대상 Identity와 선택 Product 일치. 동일 Snapshot 강제 금지 |
| Crosswalk Set | 입력에 승인 Identifier/Crosswalk가 없으므로 기존 공식 코드를 복제해 Set을 생성하지 않음 | P0에서 요구하는 정확한 Crosswalk 범위를 현우님께 확인. 필요하다는 결론이면 입력·승인 provenance·Set 구현을 #166 남은 작업에 포함 |
| Set 불변성 | 발행된 구성의 member·내용은 수정/삭제 금지. 변경은 새 구성으로 저장 | 원문·링크·manifest만 따로 수정할 수 없도록 DB 제약/권한으로 보호. 상태 enum과 READY 전이 주체는 DB 리뷰에서 지정 |

### 기존 Alias boolean 전환

| 기존 상태·근거 | 제안하는 처리 |
| --- | --- |
| is_approved=true, 상세 승인·출처 근거 확보 | 승인 근거의 대상·Source·유효성 검증 후 새 상태에 매핑. boolean만으로 승인 receipt를 만들지 않음 |
| is_approved=true, 근거 없음 | 기존 행 보존. 자동 APPROVED 이행과 검색 공개 차단. 보완 또는 명시적 이행 결정 필요 |
| is_approved=false | PENDING·REJECTED·승인 취소 중 무엇인지 추정하지 않음. 근거 없는 상태 backfill 차단 |
| alias_source·유효성·실행 출처 누락 | 임의 UNSPECIFIED·is_effective=true·실행 ID로 메우지 않음. 이행 보류 목록을 별도로 검토 |

보류는 새 DB 상태 enum을 만들겠다는 뜻이 아니다. 조사 결과로 변환 가능한 행과 근거 미확보 행을
구분하고, 검증되지 않은 행을 운영 검색에 노출하지 않는 이행 절차를 제안한다.

리뷰 요청: **현우님**은 Alias 관찰 집합/검색 집합과 정본 Alias Set의 대응, Crosswalk P0 필수 범위를,
**은영님**은 기존 Alias FK 교체 순서, 상태·불변성 제약과 기존 행 보존 방식을 검토한다.
빈 Crosswalk READY Set으로 미구현을 숨기거나 Crosswalk가 불필요하다고 임의로 범위를 축소하지 않는다.

## D-04: Component·Loader 변경안 — 근거 수집 및 리뷰 요청

확인한 근거는 현재 Parser와 합성 fixture다. 실제 Source의 role 단일성을 입증하는 자료는 없다.
현재 builder는 같은 Snapshot의 독립 Ingredient registry를 조회하며 성분을 이름에서 생성하지 않는다.

권장안:

1. 현재 `(product, ingredient, component_role)` 자연키의 의미를 유지한다. role을 제거하는 강화는
   실제 Source의 반복 성분·role·함량 분석 전에는 제안하지 않는다. 실행별 DB unique 범위는 D-02와 연결한다.
2. 현재 v2의 `strength_value`, `strength_unit`, `release_profile`, 순서를 손실 없이 보존한다.
   기존 Numeric(12,4)만으로 `010.00`·`2.50` 등 원래 문자열을 재생성하지 않는다.
3. `source_record_key`를 추가한다면 실제 Source 행 키의 안정성과 scope를 먼저 입증한다.
   현재 component_ref hash를 원본 키로 재명명하지 않는다.
4. Loader는 명시적인 FK 값과 INSERT를 사용하고 repository가 flush, adapter가 transaction을 관리하는
   안으로 제안한다. 이 경로에서는 relationship·viewonly·overlaps 정리만을 위한 변경은 하지 않는다.

김지혜가 준비할 증빙: Source 명세의 필드 경로, 합성 재현 입력, 제품·성분별 role/함량 반복 사례,
원본 키 재수집 안정성, 순서 변경 후 동일 결과와 상충 내용의 거부 테스트.
실제 Source 접근·비민감 증빙이 없으면 이 부분은 근거 미확보로 표시한다.

리뷰 요청: **현우님**은 자연키·원문 보존 의미, **은영님**은 문자열 보존 컬럼·unique 및 FK 이행을 검토한다.

## D-05: 현재 v2의 hash 저장안 — 계산 의미 유지, 물리 저장 리뷰 요청

이번 연결에서는 다음 두 종류만 저장하는 안을 제안한다. 현재 Candidate 필드 `catalog_manifest_hash`는
계속 Catalog envelope hash이며 projection hash로 바꾸지 않는다.

| 저장 자료 | 보존할 값 | 참조·검증 |
| --- | --- | --- |
| EXPORT_CHECKSUM | medication-catalog-v2, 현행 JSONL 규칙, digest, 정확한 JSONL bytes 또는 재구성 근거 | 파일의 마지막 LF까지 재계산. envelope에 기록된 export checksum과 일치 |
| CATALOG_ENVELOPE | medication-catalog-v2, catalog-manifest-envelope-v2, digest, canonical envelope payload 및 원래 manifest bytes | 자기 hash 필드 제외 payload 재계산. manifest 파일 checksum과 구분 |

권장 저장 구조는 **같은 불변 Catalog 구성에 결속된 종류별 계산 자료**다. digest만 별도 보관하거나
새 catalog_build 테이블을 먼저 전제하지 않는다. 기존 Catalog/Set 구성에 어떤 물리 키로 연결할지는
D-02·D-03 인계와 함께 은영님께 검토 요청한다.

- 논리적으로 `(구성 참조, hash_kind)`당 하나의 schema/spec·digest·계산 근거를 보존하는 안이다.
  구성 참조의 실제 unique/FK는 아직 지정하지 않는다.
- 동일 digest가 다른 실행에 나타나는 것을 전역 unique로 막지 않는다. digest 단독 FK도 사용하지 않는다.
- DB 관계에 종류·spec·대상 결속이 있어야 하고, application은 bytes 재계산까지 수행한다.
- Candidate에는 전체 CatalogExportArtifacts를 인계한다. DB 물리 metadata를 기존 v2 envelope에 삽입하지 않는다.
- Candidate projection·Runtime medication manifest는 최신 v2 초안의 **새 계약 버전 제안**으로 유지한다.
  승인 Identifier 집합·Entry 본문 hash·projection allowlist·canonical fixture 없이 계산하지 않는다.
- Runtime Catalog 전용 참조는 해당 의미 계약이 준비된 뒤 #166에서 추가하고 은영님 리뷰를 받는다.
  현재 envelope hash를 그 값으로 대입하거나 placeholder 컬럼/값으로 완료 처리하지 않는다.

리뷰 요청: **은영님**은 계산 자료 저장 위치·구성 FK·유일성·불변성 보호를 검토한다.
**현우님**께는 이미 정리한 v2 유지 여부를 다시 묻지 않고, DB 왕복이 기존 인계 bytes/의미를 보존하는지
검토 요청한다. 새 projection 전환은 이번 v2 저장안과 분리해 남은 범위로 명시한다.

## D-06: transaction·실패·재시도 변경안 — 리뷰 요청

### transaction 소유와 저장 흐름

실제 adapter가 transaction을 소유하고 `save_build`는 commit 확인 이후에만 정상 반환하는 안을 제안한다.
기존 서비스/포트의 인자 구조는 유지하며 repository의 flush만으로 저장 성공을 보고하지 않는다.
외부 세션을 주입받는 경우 호출자가 이미 연 transaction의 일부를 임의 commit하는 구현은 피하고,
adapter 생성·호출 경계에서 소유자를 하나로 제한한다.

1. DB 밖에서 입력·구성원·v2 artifacts·저장 자료를 검증한다.
2. 외부 승인 verifier를 호출하되 그 응답만으로 이후 DB transaction의 현재성을 보장한다고 가정하지 않는다.
3. transaction 안에서 **인계된 요청/실행 key**로 같은 요청을 직렬화한다. 실제 키·잠금 대상은 D-02 이후
   지정한다. envelope hash만으로 요청 키를 만들지 않는다.
4. 선택된 Source/Publication 참조와 승인 근거 revision의 현재 적격성을 재확인한다. 승인 철회 writer와
   같은 잠금 또는 버전 조건을 공유해야 한다. 실제 승인 저장소와 철회 프로토콜이 없으면 이 검증은 미완료다.
5. Identity를 자연키 순으로 upsert하고 구성원 → Alias/Component → Search Entry → Set/member →
   manifest 참조를 적재한다. 공통 잠금 순서는 DB 검토 후 고정해 교착을 줄인다.
6. 모든 참조·count·계산 자료 결속을 확인하고 한 번 commit한다. commit 뒤 결과를 반환한다.
   저장 성공이 실제 Runtime 활성화나 Source 승인 부여를 의미하지 않는다.

### 재시도와 실패 처리

| 발생 조건 | 제안 동작 | 검증 기준 |
| --- | --- | --- |
| 같은 확정 요청 key·같은 완전한 내용 재시도 | 기존 불변 결과를 재조회·대조 후 재현 | 구성원·Set·manifest 중복 0건. 읽기 시 현재 승인 gate는 별도 적용 |
| 같은 요청 key·다른 내용 | 기존 결과 보존, 충돌로 실패 | 기존 bytes·참조 불변. 정확한 오류 코드/외부 매핑은 adapter 리뷰에서 지정 |
| mapping·구성원 검증 실패 | 기존 REJECTED 경계 유지, 저장 호출 없음 | export·부분 구성원 없음 |
| 중간 INSERT 또는 commit 이전 오류 | 이번 Catalog transaction 전체 rollback | 새 구성원·Set·manifest·신규 Identity의 부분 commit 없음 |
| commit 직후 통신 단절 | 성공/실패를 추측하지 않고 같은 확정 key로 재조회 | 완전한 결과일 때만 재현, 판단 불가 시 성공 반환 금지 |
| deadlock/serialization failure | 전체 transaction을 제한적으로 재시도하는 안 | 부분 재개 금지, 매 시도 참조/승인 적격성 재검사. 한도·분류는 adapter 설정으로 명시 |
| 승인 철회 또는 Source 부적격 전이와 경합 | 공유 잠금/버전 검사로 한 순서를 결정 | 철회가 먼저 확정된 경우 부적격 결과 소비 차단. 먼저 저장됐어도 후속 소비 시 현재성 재검사 |
| rollback 후 실패 기록 | 실패 transaction 밖의 별도 감사 경계에서 기록 | 감사 실패를 성공으로 숨기지 않음. 원래 오류 원문·Alias·Source 원문 미기록 |

실패 기록에는 승인된 요청/실행 참조·처리 단계·고정 오류 분류·시도·시각 등 안전한 metadata만 남기는
안을 제안한다. 새로운 감사 테이블이나 임의 오류 enum을 이 문서에서 확정하지 않는다. 사용 가능한
기존 감사 저장소와 접근 권한은 DB 리뷰에서 지정한다.

파일은 우선 메모리의 검증된 artifacts를 사용하는 안이다. 별도 파일 저장을 붙일 경우 commit 전 공개하지
않고 잔여 파일의 식별·정리 책임을 정한다. #347 Source Artifact 삭제가 Catalog 파일도 처리한다고 간주하지 않는다.
NOT_APPROVED/STALE 자료를 저장하더라도 검색 적격 상태로 승격하지 않는다.

리뷰 요청: **은영님**은 transaction 소유, 승인/Source writer와의 경합 제어, 실패 감사 저장 위치를,
**현우님**은 재시도 결과와 현재 승인·Source 결속·소비 gate 의미를 검토한다.

## 4단계 진행 범위: D-02 비의존 스키마 기반

D-02 미확정 유지, 별도 run 임의 생성 금지, ingestion run으로 정본 `normalization_run_id` 대체 금지가
현재 합의다. 이 경계를 지키면서 D-02 실행/Publication key 없이 완결되는 안정 Identity·Alias 상태·
Search Entry 기반을 revision `166a7b8c9d0e`로 구현했다.

- Product·Ingredient 안정 Identity와 공식 코드 결속
- Alias의 안정 Identity 대상 전환, Alias 자체 Snapshot provenance와 상태·출처·유효성
- Product와 Alias가 서로 다른 Snapshot에 있을 수 있는 Search Entry Identity 결속
- Product 이름과 승인·활성·유효 Product Alias의 검색 적격성·정규화 문자열 검사
- 기존 coded Product·Ingredient backfill과 근거 없는 Ingredient/Alias 변환의 fail-closed 중단

이 revision에는 실행 ID·Publication 구성 key, Set/member, hash 저장, 실제 adapter transaction을 넣지
않는다. 실행별 unique/composite FK도 D-02 인계 전에는 현재 Snapshot 범위를 임의로 대체하지 않는다.
현재 `down_revision`은 작성 시점 head에 연결한 임시 값이며, 합의된 Evidence/Citation migration이
병합되면 PR 제출 전에 최신 head로 재연결하고 전체 migration 회귀를 다시 확인한다.

구현과 격리 PostgreSQL 검증 결과는
[Catalog Identity·Alias·Search Entry DB 기반 검증](../../../testing/catalog-identity-alias-search-schema-166.md)에
기록한다. 이 기반 구현은 D-02나 #166 전체 DB 통합 완료를 의미하지 않는다.

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
