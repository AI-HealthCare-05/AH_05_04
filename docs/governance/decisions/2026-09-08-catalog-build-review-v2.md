# #329 Catalog 리뷰 반영 v2

상태: 리뷰 요청에 따른 구현안, 지정 리뷰어 최종 검토 대기. Production 승인 아님.

- 근거: PR #329의 정현우 변경 요청(승인 자동 승격, 독립 Ingredient, Alias dedupe, mapping 실패 응답).
- 구현: 김지혜. 검토: 정현우(Candidate 입력·hash), 송은영(DB·승인 포트).
- Component에서 Ingredient를 만들던 입력을 독립 registry 참조로 바꾼다.
- 같은 제품 Alias는 출처 행을 보존하고 적격 검색 항목만 결정적으로 하나 선택한다.
- mapping 실패는 기존 Catalog failure reason으로 REJECTED 처리한다.
- 승인 verifier가 없으면 미승인으로 유지한다. 검증된 receipt와 gate 상태를 manifest에 포함하고 v2로 구분한다.
- 실제 receipt 제공 adapter, normalization DB 모델 및 외부 정본 projection hash와의 최종 대응은 별도 후속이다. 임의 운영 승인을 만들지 않는다.

상세 명세: `docs/contracts/targets/post-mvp-1/catalog-build-v2.md`.

## 추가 리뷰: 소비 경계와 보험 식별자 제외

근거: https://github.com/AI-HealthCare-05/AH_05_04/pull/329#pullrequestreview-5138112718

Candidate Index의 public build 입력을 raw typed Catalog에서 전체 CatalogExportArtifacts로 변경한다. 소비 경계에서 hash·manifest·JSONL·typed 필드 결속을 검증하고, raw 입력은 기존 CATALOG_MANIFEST_INVALID로 거부한다. 검증을 호출자 관례에 맡기지 않는다.

P0 code system은 Product MFDS_ITEM_SEQ, Ingredient MFDS_INGREDIENT_CODE만 허용하는 구현안으로 고정하고, 목록 확장은 Source/Candidate 검토를 거친다. 기존 테스트 중 표시 문자열 보존 테스트의 비표준 MFDS_INGREDIENT 표기는 합성 fixture의 MFDS_INGREDIENT_CODE와 맞췄다. 현재는 리뷰 요청에 따른 구현·계약 보완이며 운영 승인이나 확정된 외부 코드 체계 확대가 아니다.


## 추가 리뷰: 원문 보존과 Candidate NFC 경계

2026-09-08 정현우의 PR #329 리뷰 및 #342의 원문 보존 기준을 반영한 구현안이다.
제품 `product_name/strength_text/dosage_form/manufacturer_name`, 성분 `ingredient_name`, Alias
`alias_text`, Search Entry `display_text`는 NFD를 포함해 Source 원문을 보존한다.
normalized 필드·공식 Identity·참조·버전·build config의 NFC 검증은 유지한다.
Candidate hash 직렬화에서도 전체 문자열 NFC 변환을 제거해 원문 바이트를 결속한다.
기존에 허용하던 NFC 입력의 hash는 동일하다. 새로 허용되는 NFD 표시값의 hash는 NFC 표시값과
구분한다. 승인·JSONL·manifest 검증은 우회하지 않는다. 정현우의 최종 재리뷰 대상이다.
