# #166 D-04 MFDS Loader·저장·Candidate 인계

- 상태: Proposed / 구현·합성 검증 완료, PR #477 `c58f0968` 정현우 담당 범위 승인. 병합·별도 전문 검토·MFDS 공식 의미·운영 활성화 승인은 구분한다.
- 확인일: 2026-09-13
- 기준: develop `f56a632`, `feat/166-d04-mfds-loader`
- 구현: 김지혜. 담당 리뷰어: 정현우(`ceohwj`). DB·migration 전문 검토: 송은영(`phina-io`).
- 계약: [Catalog DB 연결안](../../contracts/proposed/post-mvp-1/catalog-db-integration-v2.md)
- 변경 결정안: [Component 관찰 출처](../../governance/decisions/2026-09-13-component-observation-handoff.md)
- 실측 근거: [MFDS 관찰 검사](../../testing/mfds-component-key-audit-166.md). 공식 의미 승인과 구분한다.
- 합의·검토 근거: [D-04 결정 문서의 원문 및 공개 리뷰](../../governance/decisions/2026-09-13-component-observation-handoff.md#합의와-검토-근거). PR #477 HEAD `c58f09686d3a72567793ee237abeb500ab04e710`의 정현우 담당 범위 승인 기록을 연결하며 전문 검토·운영 승인은 별도다.

## 반영한 방향

MTRAL_CODE는 MFDS 출처 범위의 Ingredient 코드로 연결한다. 전역 정규화 Identity·장기 안정성을
승인한 것으로 해석하지 않으며, 같은 성분명으로 다른 원료코드를 합치지 않는다.
ITEM_SEQ·TAMT_SEQ·MTRAL_SN 원문을 관찰 키로 보존한다. 총량 그룹과 함량·단위를
Export, DB 복원 및 Candidate 반환까지 전달한다. 공식 총량 의미나 숫자 정렬을 자동 추론하지 않는다.

제품 목록과 성분 상세의 Snapshot을 구분하고 새로운 상세 Snapshot은 새 관찰 버전으로 저장한다.
빈 성분 행은 Ingredient·Component로 생성하지 않는다. 원문 값·사유·입력 건수·중복 건수를
검사 결과로 반환하고, 제외 행이 하나라도 있으면 부분 Catalog를 저장하지 않는다.
전문/일반의약품 분류는 후속 범위를 유지한다.

## 구현 경로

1. `catalog/mfds_loader.py::load_mfds_catalog`는 제품 입력, 독립 Ingredient 매핑,
   명시적 관찰별 순서와 순서 계약 버전, 제품·상세 Receipt 및 상세 canonical JSON을 받는다.
2. 두 Receipt의 provenance, 상세 checksum·canonical 형식, 전체 상세 행, 제품·원료·순서의
   정확한 입력 범위를 검사한다. 유효한 typed 제품 입력의 생산 책임은 호출자에게 있다.
3. `build_catalog_candidate`가 구성원 정합성과 승인 포트를 확인한 뒤 기존 저장 Repository를 호출한다.
4. `ai_worker/adapters/sqlalchemy_catalog_write_support.py`는 실제 Source Receipt 재검증,
   구성원 및 Set/member/hash 저장·read-back을 기존 단일 transaction 안에서 수행한다.
   상세 artifact의 checksum·계약 버전을 관찰에 결속하고 잠금 이후 실제 DB Receipt와 대조한다.
5. 복원 후 Candidate는 관찰 메타데이터가 있는 전체 Component를 `components`로 인계한다.
   검색·순위 알고리즘, Runtime 활성화와 함량 계산은 추가하지 않는다.

`catalog/`는 `ai_worker/tasks/rag/catalog/`를 뜻한다.

## DB·Export 제안과 구현

- Migration `166f30415263`은 기존 head `e8c41a09d652` 뒤에 추가한다. 적용 이력은 수정하지 않는다.
- Component 자체 출처는 상세 Snapshot, 별도 두 참조 출처는 Product/Ingredient 행과 composite FK로 결속한다.
- UNIQUE는 `(product_id, source_snapshot_id, display_order)`다. 재수집 시 과거 Set을 덮어쓰지 않는다.
- `observation_json`에 개별 원문 키·총량 그룹·원료코드·함량·단위·출처·순서 계약 버전을 보존한다.
- 기존 행의 두 참조 출처만 기존 FK가 증명하는 Snapshot으로 채운다. 과거 관찰 필드는 추론하지 않는다.
- downgrade는 관찰 JSON, 서로 다른 출처 또는 이전 고유성에 맞지 않는 관찰 버전이 있으면 중단한다.
- 새 필수 참조 열을 모르는 구버전 Writer는 쓰기에 실패한다. 적용 시 Catalog Writer 중지 →
  migration·새 코드 적용 → 저장/복원 점검 → 재개 순서가 필요하다. 운영 적용은 수행하지 않았다.
- 관찰 정보가 있는 Export는 `medication-catalog-v3`다. 관찰 정보가 없는 기존 v2 bytes/hash는 그대로다.
  `catalog-manifest-envelope-v2` 계산 규칙은 유지하며 projection/runtime hash를 추가하지 않는다.

위 내용은 지혜님이 제안·구현한 PR 검토안이다. DB 검토를 이미 받았다고 표시하지 않는다.
비즈니스 로직·무결성 검증은 Python Service/Repository와 transaction에서 처리하며
일반 FK·UNIQUE·CHECK·권한 경계를 사용한다. 신규 RLS·Trigger·업무용 DB 함수는 없다.

## 실입력 연결에 남은 조건

현재 등록된 Source 수집 Operation은 제품 목록 중심이다. 주성분 상세 Operation 등록,
실제 API 호출·Raw Artifact 수집·상세 Snapshot 생성은 이 Loader가 수행하지 않는다.
새 상세 canonical artifact 계약은 내부 제안이며 기존 제품 목록 Receipt를 대신 붙이면 안 된다.
합성 DB 통합 테스트의 상세 Receipt는 실제 MFDS 수집·사용 승인 증빙이 아니다.

실제 사용에는 승인된 상세 Source의 canonical artifact와 Receipt, 독립 Product/Ingredient 입력,
명시적 순서 계약이 필요하다. 공식 TAMT_SEQ/MTRAL_SN 의미와 복수 총량 그룹 사례를 확인하기 전
자동 표시 순서 규칙은 활성화하지 않는다. 기존 상세 API 관찰 결과만으로 공식 의미를 확정하지 않는다.

검사 결과는 호출자에게 반환하며 새로운 감사 저장소를 구현한 것은 아니다. 호출자는 원본
artifact와 검사 보고서를 보존해야 한다. 제한 적재를 하려면 별도의 포함·제외 정책과 명시된
대상 범위가 필요하며, 이 Loader는 전체 입력에서 불량 행을 조용히 제외하는 모드를 제공하지 않는다.

[D-03 Crosswalk](../../governance/decisions/2026-09-13-catalog-crosswalk-scope.md)는 현재 P0 소비 경로가
없어 즉시 구현 범위에서 제외했다. D-02 실행 이력, D-05 projection/runtime hash 및 실제
승인·철회·감사 저장소를 이번 작업의 완료 범위에 포함하지 않는다.

검증 결과는 [D-04 검사 기록](../../testing/mfds-loader-handoff-166.md)에 연결한다.
