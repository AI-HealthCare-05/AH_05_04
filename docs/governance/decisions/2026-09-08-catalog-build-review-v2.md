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
