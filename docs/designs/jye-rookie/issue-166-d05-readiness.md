# #166 D-05 전환 선행 조건 1~4 점검·보완안

- 상태: **로컬 조사·Proposed. 새 hash 계약 승인/구현 아님.**
- 기준: 원격 develop fetch 후 `f10ca016cb03161019c373e02bdfc472ea5461c9` (#477 병합 포함).
- 검토 자료: 사용자 제공 `issue-166-d05-hash-calculation-draft-v2.md`, 2026-09-08 검토 반영본.
- 원문 파일 SHA-256: `06ababf9bab2fa2ce16dc36a990fa700abc3b923fa9d46644b7030edc9712c7f` (첨부 문서 식별용이며 Catalog hash가 아님).
- 원문은 Desktop 파일 그대로 보존한다. 본 문서는 원문을 대체하거나 승인 상태를 승격하지 않는다.
- 구현 담당 김지혜 / Candidate·Runtime 의미 확인 정현우 / DB 참조·migration 검토 송은영.
- AGENTS.md·CONTRIBUTING.md 및 보안·개인정보 원칙을 따른다. 신규 RLS·Trigger·업무용 DB 함수 없음.

## 후속 답변 반영 상태

정현우 답변에 따른 현재 범위는 [D-05 전환 보류·재개 조건](../../governance/decisions/2026-09-13-catalog-d05-transition-scope.md)을 따른다.
전체 전환과 Runtime 구성·연결은 후속 보류다. 아래 후보와 질문은 답변 전 조사 이력으로 보존하며,
확인받은 방향을 다시 미답변으로 취급하거나 신규 계산 구현 승인으로 확대하지 않는다.

## 1. 선행 조건 점검 결과

| 초안의 선행 조건 | 최신 코드에서 확인 | 현재 판정 | 이번에 준비한 내용 |
| --- | --- | --- | --- |
| 승인 Product Identifier 집합 | ProductIdentity의 PRODUCT/MFDS_ITEM_SEQ/canonical_code는 존재. CatalogProduct·Export에는 별도 승인 Identifier 집합/receipt 없음 | 초안에서 요구한 별도 집합은 미충족. 현재 Identity를 같은 것으로 간주하지 않음 | D-03 P0 제외 결정과 조정할 선택지 |
| 명시적인 Entry 본문 hash | CatalogSearchEntry에는 본문 hash 필드 없음. Candidate 내부 member_content_hash는 존재 | 새 의미의 본문 hash는 미충족 | 기존 payload 분해와 독립 본문 필드 후보 |
| projection 필드 allowlist | 현재 Candidate가 소비·보존하는 필드는 식별 가능. 새 projection 전용 정본 없음 | 미확정 | 아래 포함·제외·별도 binding 표 |
| canonicalization fixture | 기존 v2 고정 bytes·digest 및 손상/Unicode 검증 있음. 신규 projection용 bytes·digest 없음 | v2 검증 기반 확보 / 새 계약은 미충족 | 기존 사례 대응과 신규 기대 관계 시나리오 |

여기서 “없음”은 이 develop의 Catalog 모델·export·Candidate 구현 및 관련 계약을 조사한 결과다.
공식 기관 자료나 팀의 비공개 자료까지 없다는 주장이 아니다.

## 2. 기존 초안에 보충해야 하는 변화

1. **#477 관찰 v3가 생겼다.** `medication-catalog-v3`는 Component 관찰 자료를 포함한 export이며,
   envelope spec은 여전히 `catalog-manifest-envelope-v2`다. D-05 projection 전환이 아니다.
   신규 D-05 spec 이름을 관찰 v3와 혼동하지 않고 v2·관찰 v3 입력의 처리 방식을 명시해야 한다.
2. **D-03 범위가 정리됐다.** P0는 MFDS 제품/성분 Identity를 사용하고 Crosswalk·보험코드 경로는 제외됐다.
   하지만 이것만으로 D-05의 승인 Product Identifier 선행 조건이 충족/삭제됐다고 판단할 수 없다.
3. **저장 위치는 더 이상 백지 상태가 아니다.** `rag_catalog_set_hash`가 Set FK, 종류, schema,
   contract_spec_version, digest, target, canonical_bytes를 저장한다.
4. **Runtime에도 기존 Catalog 결속 필드가 있다.** Bundle의 catalog_version/catalog_manifest_hash와
   Candidate Index 자체 manifest_hash를 구분해 사용한다. 신규 medication Catalog manifest 의미와
   기존 필드를 동일시하거나 단순 컬럼 추가만으로 연결 완료라고 표시하지 않는다.

## 3. 승인 Identifier 조건 — 현우님 확인안

[현재 D-03 결정](../../governance/decisions/2026-09-13-catalog-crosswalk-scope.md)은 보험 Identifier,
일반 Crosswalk, D-04 원료코드 연결을 별개 범위로 구분한다.

제안: **현재 P0에서는 별도 Identifier 입력이 실제로 필요해질 때까지 새 hash 전환을 보류하고
기존 envelope 인계를 유지한다.** 다만 현재 소비 목적상 projection 분리가 필요한 경우에는,
MFDS Product Identity만을 대상으로 한 축소 계약을 새로 승인할 수 있는지 확인한다.
이 경우 원문 1절의 “승인 Identifier 집합 필수” 조건 변경을 Decision에 명시해야 한다.
빈 승인 집합, 가짜 receipt, Product Identity를 Identifier 집합으로 복제하는 대체 구현은 하지 않는다.
별도 Identifier가 여전히 필수라면 허용 코드 체계·승인 자료·공급 주체가 무엇인지 확인한다.

## 4. Entry 본문 hash 및 projection allowlist 초안

기존 `candidate_index._member_payload()`는 identity·display/normalized text·Product 표시 속성뿐 아니라
product_ref·entry_ref·alias_ref, Snapshot ID, catalog_version, catalog_manifest_hash를 포함한다.
따라서 `member_content_hash`는 새 의미 projection 본문 hash로 그대로 재사용할 수 없다.
현재 `member_key`도 entry_ref를 포함하므로 새 semantic key와 같다고 가정하지 않는다.

아래는 새 계약이 필요하다고 확인될 때 검토할 필드안이다. hash 함수·필수 DTO를 추가하지 않는다.

| 현재 필드 | 새 projection/본문 제안 | 근거·확인 사항 |
| --- | --- | --- |
| identity.entity_type/code_system/canonical_code | projection 구성원 key에 포함 | 현재 P0 공식 제품 Identity; DB UUID 대체 금지 |
| entry_type | 구성원 key에 포함 | PRODUCT_NAME/APPROVED_ALIAS 구분 |
| normalized_text | 구성원 key에 포함 | 검색 의미. normalization_version도 아래처럼 결속 |
| normalization_version | projection 상위 입력에 포함 | 동일 문자열이라도 적용 spec 구분 |
| display_text | 별도 Entry 본문에 포함 | 현재 Candidate 표시값 보존; NFC/NFD 원문 차이 유지 |
| product_name | 본문에 포함 | Alias 경로에서도 Product 표시 의미 보존 |
| strength_text, dosage_form, manufacturer_name | 본문에 포함 제안 | 현재 Index member가 보존하는 제품 속성. 단순 검색 문자열보다 넓은 범위임을 확인 필요 |
| 승인 Identifier 집합 | 조건 1 결정 후 추가 여부 확정 | 빈 배열/추론 값으로 채우지 않음 |
| source_snapshot_id들, Source version | projection 밖 별도 binding 유지 제안 | 같은 의미·다른 출처는 같은 projection이어도 별도 출처 검증 필수 |
| catalog_version, catalog_manifest_hash | projection 밖 기존 envelope binding 유지 제안 | envelope 변화를 새 projection 입력으로 재포함하면 분리 목적이 약해짐 |
| product_ref, entry_ref, alias_ref | 의미 hash에서는 제외 제안 / 원래 관계는 보존 | 재수집·참조 변경만으로 의미 hash 변화 여부를 구분 |
| review_status, status, is_effective, receipt | 적격성·승인 gate에서 검증 | 구성원이 제외되면 의미 집합도 변함; hash만으로 승인 인정 금지 |
| count·검증 결과·export checksum | 별도 검증/envelope 유지 | 부분 자료를 의미 hash만 맞춰 통과시키지 않음 |
| Component 관찰·총량·원본 키·상세 Snapshot | Candidate 본문에서는 제외, Runtime 포함 범위는 별도 확인 | 실제 의미 소비 여부에 따라 결정. #477의 출처 검증은 계속 유지 |
| normalization/build 실행 ID | D-02 결정 대기 | ingestion run으로 대체하지 않음 |

제안하는 Entry 본문은 원문 표시값과 제품 표시 속성만 담고, 독립된 kind/spec으로 계산한다.
projection 구성원은 Identity·entry_type·normalized_text·본문 hash를 연결한다.
양쪽이 서로의 hash를 넣는 순환 참조를 만들지 않는다.

**추가로 빠진 결정:** 위 semantic key가 같은 Entry가 여러 출처/내부 ref로 존재하면
어떻게 할지 정해야 한다. 현재 Index는 ref를 사용하므로 새 key로 변경할 때 자동 dedupe하면 안 된다.
동일 key·상충 본문은 거부, 동일 key·동일 본문은 별도 provenance binding에 모든 출처를 보존하고
의미 구성원은 하나로 만들자는 안을 검토 요청한다. 아직 이 병합 규칙을 구현하지 않는다.

## 5. canonicalization fixture 준비

### 기존 자료와 이번 실행

- `tests/fixtures/rag/catalog/hash-v2/`: catalog.jsonl, envelope-payload.json, manifest.json, expected.json.
- `test_hash_contract_v2.py`: 고정 기대 bytes/digest, 입력 순서, LF/CRLF/BOM, hash 종류 오대입,
  manifest binding 변조, 중복 JSON key, NFC/NFD 표시값 차이.
- `test_restore_bytes.py`: 기존 v2 golden 복원과 손상 거부.
- `test_component_order.py`: 순서 충돌·반복 성분·원본 키 및 배열 순서 변화 검증.
- 위 세 파일을 이 worktree에서 실행: **39 passed in 0.24s**.
- 기존 테스트 재실행이다. 새 projection 테스트·실제 승인/DB 통합·전체 CI 통과 증빙이 아니다.

### 신규 golden에 추가할 관계·입력 사례 — 미실행/예상 digest 미발급

| 사례 | 기대 관계 제안 | 확정 필요 |
| --- | --- | --- |
| 같은 구성원 입력 순서만 변경 | projection 동일 | 최종 semantic key·중복 규칙 |
| Identity/entry_type/normalized_text 변경 | projection 변경 | allowlist |
| 표시값·제품 속성 변경 | 본문/projection 변경 | 제품 속성 포함 범위 |
| 표시값 NFC→NFD | 본문/projection 변경 | 원문 bytes 보존 승인 |
| normalized_text가 NFC 아님 | 입력 거부 | 정상화 경계 정렬 |
| NULL/누락/빈 문자열 | nullable schema대로 구분/거부 | 정확한 필드 타입·requiredness |
| JSON 중복 key·미허용 key·NaN·bool count | 거부 | 문자열 escaping·숫자·정렬의 완결된 spec |
| 내부 ref/Snapshot만 변경, 의미 동일 | projection 동일, binding은 별도 재검증 | provenance 분리 정책 |
| Source 승인 철회/만료 | hash 동일 가능, 승인 gate는 거부 | 실제 승인 저장소 연결은 별도 후속 |
| 동일 semantic key·상충 본문 | 거부 | 새 충돌 규칙 |
| 동일 key·동일 본문·여러 출처 | 명시적 집합 병합 + 출처 전부 보존 제안 | 신규 중복 규칙 승인 |
| kind/schema/spec만 변경 | 새로운 hash 또는 미지원 버전 거부 | 확정 버전명 |
| Component 관찰·Runtime 전용 구성원 변경 | Runtime hash 변경, Candidate 의미 동일이면 projection 유지 제안 | Runtime 범위·관찰 순서 의미 |
| Index 설정만 변경 | Index hash 변경, Catalog projection 유지 제안 | #167 생산/소비 fixture 정렬 |
| 실행 provenance만 변경 | D-02 포함 정책 확정 후 기대 결과 결정 | D-02 대기 |
| v2·관찰 v3·신규 D-05 입력 | 명시된 버전별 검증, 이름 추정 변환 금지 | 전환/구버전 정책 |

새 golden에는 고정 합성 입력, canonical bytes, 독립 검산한 digest, binding/gate 결과를 함께 보존한다.
아직 의미·중복·버전 규칙이 미확정이므로 새 bytes/digest를 발급하지 않는다.
기존 v2 fixture를 새 projection fixture로 이름만 바꿔 사용하지 않는다.

## 6. 은영님 확인 시 이미 구현된 부분과 남은 차이

현재 `RagCatalogSetHash`의 실제 필드는 set_id/hash_kind/schema_version/contract_spec_version/
digest/target/canonical_bytes다. 초안의 hash_value·canonicalization_spec_version과 개념상 대응하지만
현재 물리 이름은 다르다. 그 이름을 새로 바꾸자는 제안이 아니다.

종류 CHECK는 EXPORT_CHECKSUM/CATALOG_ENVELOPE, target CHECK는 catalog_jsonl/envelope_payload만 허용한다.
PK는 (set_id, hash_kind)다. 따라서 초안의 “kind/spec만 늘리면 저장 구조 유지”는 자동으로 성립하지 않는다.
새 종류/target 추가와 같은 Set·kind에 여러 spec을 보존할 필요가 있는지 먼저 확인한 뒤 migration 범위를 정한다.
현재 Canonical bytes 재계산과 Set 결속은 구현돼 있으므로 저장 위치를 처음부터 재질문하지 않는다.
Runtime 기존 catalog_manifest_hash와 새 전용 manifest 참조의 역할도 구분해서 변경안을 검토한다.

## 7. 현우님께 확인할 최종 질문

1. 첨부 v2의 조건 1을 P0에서도 유지할지, 새 hash 전환 자체를 보류할지 또는 Identity 한정 축소 계약을 검토할지.
2. 위 Entry 본문/allowlist 및 provenance 분리안, 새 semantic key 중복 처리에 동의하는지.
3. Runtime 구성원 중 확정된 Product·Ingredient·Component·Alias·Search Entry 범위와 관찰 정보 결속 방식.
   Crosswalk는 현재 제외하고 실행 provenance는 D-02 대기로 명시하는 안의 적용 가능 여부.
4. 관찰 v3와 별개인 새 D-05 계약의 버전·구버전 처리 및 Catalog/Candidate/Runtime 재계산 책임.

현재 문서는 합의된 것으로 간주하지 않는다. 답변 후 남은 canonical 세부값과 golden을 완성하고,
저장 변경안은 은영님 리뷰에 연결한다. 원격 Issue/PR·원문 파일·코드·migration은 변경하지 않는다.

## 코드 근거

- [Catalog 타입](../../../ai_worker/tasks/rag/catalog/types.py)
- [Export schema·envelope](../../../ai_worker/tasks/rag/catalog/export.py)
- [Candidate member 계산/검증](../../../ai_worker/tasks/rag/candidate_index.py)
- [Hash DB 모델](../../../backend/app/models/rag_catalog.py)
- [저장 계획의 hash 재계산](../../../ai_worker/tasks/rag/catalog/storage.py)
- [Runtime 구성 계산](../../../ai_worker/tasks/rag/runtime_bundle_builder.py)
- [Runtime 저장 모델](../../../backend/app/models/rag_runtime.py)
- [D-04 관찰 v3 결정](../../governance/decisions/2026-09-13-component-observation-handoff.md)
