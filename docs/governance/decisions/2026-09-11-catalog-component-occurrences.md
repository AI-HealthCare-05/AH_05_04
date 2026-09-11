# D-04 Component occurrence 전환안

- 상태: Proposed — 구현·테스트를 동반한 리뷰안, 승인 또는 운영 전환 선언 아님
- Issue: #166
- 구현: 김지혜. PR 책임 리뷰어: 정현우 1명 (사용자 지정). Component/Candidate 의미 확인 담당: 정현우.
- 기준 develop: `7f6cc85` (#454 병합 포함)
- 계약: [Catalog DB 연결안](../../contracts/proposed/post-mvp-1/catalog-db-integration-v2.md)

## 문제와 결정안

기존 `(product, ingredient, role)` UNIQUE와 같은 필드 기반 component_ref는 같은 성분의
서로 다른 발생 행을 구분하지 못한다. DB 제약만 바꾸면 ref 충돌이 남으므로 함께 전환한다.

1. `CatalogComponentInput.source_record_key`를 선택 입력으로 추가한다. 없는 기존 입력은
   기존 참조 계산을 그대로 사용한다. 있는 입력은 nonblank·trimmed·NFC 문자열을 요구하고,
   기존 stable-ref 인코딩에 `reference_spec=catalog-component-source-key-v1`, `product_ref`,
   `source_record_key`를 넣어 component_ref를 계산한다. 순서·함량으로 원본 키를 만들지 않는다.
2. Python 검증은 component_ref 중복과 `(product_ref, component_order)` 중복을 거부한다.
   정확히 같은 입력의 기존 dedupe는 유지한다. 명시적인 양의 order를 보존하며 연속 순서나
   배열 index 기반 재번호 규칙을 추가하지 않는다.
3. DB는 `(product_id, display_order)` UNIQUE로 전환한다. product/ingredient 조회용 index를
   남기고 nullable release_profile을 추가한다. Loader와 Repository 조회도 같은 키를 사용한다.
   동일 Set 재사용 시 실제 행과 전체 canonical 자료를 대조하며 기존 내용을 덮어쓰지 않는다.
4. forward revision `e8c41a09d652`는 테이블 쓰기 잠금 후 기존 순서 충돌을 검사한다.
   충돌은 무보정 중단한다. downgrade는 반복 성분 또는 non-null release_profile이 있으면
   손실 위험으로 중단한다. 기존 행의 참조·order·hash를 일괄 재작성하지 않는다.

## 호환성·원본 근거

v2 export/envelope schema, canonicalization과 digest 계산은 유지한다. 기존 golden bytes도
유지한다. 새 원본 키를 제공한 입력의 component_ref와 결과 digest는 달라진다. 기존 저장된
Set은 기존 canonical bytes로 복원한다. 입력 전환을 과거 Set의 재사용이라고 주장하지 않는다.
원본 키 자체의 별도 DB 컬럼은 추가하지 않는다. Source 원문이 근거이고, Catalog는 계산된
component_ref와 Source Snapshot 결속을 보존한다. 원문 없는 ref를 원본 키 증빙으로 사용하지 않는다.
Snapshot이 같고 제품별 order가 같지만 기존 내용과 다른 경우 현재 Loader는 거부한다.
새 normalization 실행을 같은 Snapshot 위에 덮어쓰는 지원은 D-02 후속이다.

## MFDS 적용 경계

사용자 제공 샘플은 1페이지 50행을 두 번 읽은 통계이며 완전한 Source Receipt가 아니다.
`ITEM_SEQ + TAMT_SEQ + MTRAL_SN` 후보키 불완전 3행, 완전 키 중 중복 그룹 0건이다.
재조회 샘플 hash는 같지만 전체 고유성·장기 안정성·공식 Ingredient 매핑은 증명하지 않는다.

`map_mfds_component()`는 승인된 원료→Ingredient 입력과 명시적 order를 호출자가 제공할 때만
상세 행을 변환하는 함수다. ITEM_SEQ·TAMT_SEQ·MTRAL_SN·MTRAL_CODE·QNT·INGD_UNIT_CD가
빠지거나 원료코드가 제공된 매핑과 다르면 예외로 중단한다. 문자열 원본 키와 함량은 보존한다.
MTRAL_CODE를 공식 Ingredient code로 자동 채택하지 않고 이름으로 추론하지 않는다.
CPNT_CTNT_CONT를 방출형으로 해석하지 않는다. 불완전한 3행을 버리고 성공시키지 않는다.
이 함수는 아직 실제 수집 파이프라인에 자동 연결하지 않는다. 호출자의 매핑 제공은 승인
검증 자체가 아니며 기존 Source Receipt·Catalog 승인 경계를 우회하지 않는다.

실제 MFDS 자동 적재 전 남은 근거는 전체 입력의 키 검증, 원료→공식 Identity 매핑,
명시적 order 규칙이다. 추가 인증키 공유 없이 현재 코드·합성 검증은 진행할 수 있다.
RLS·Trigger·업무용 DB 함수·새 run·Crosswalk·projection hash·실제 승인 저장소는 추가하지 않는다.

## 적용 순서

리뷰 후 migration을 먼저 적용하고 새 ORM/Writer를 배포한다. 전환 전에 기존 DB의
제품별 order 충돌을 점검하며, 발견된 행은 승인된 원본 근거로 별도 해결한다. 자동 삭제나
순서 재번호로 배포를 통과시키지 않는다. 일반 컬럼 추가만으로 새로운 Writer 권한을
부여하지 않으며 기존 Catalog 전용 INSERT/SELECT·Runtime SELECT 정책을 유지한다.
상세 Operation과 기존 제품 목록 Operation의 Snapshot을 임의로 같게 취급하지 않는다.
현재 Component의 동일 Snapshot FK에 맞는 검증된 입력 구성이 필요하다.

## MFDS 실측 이후 의미 확인안

[키 없이 대조한 처리 제안](../../designs/jye-rookie/issue-166-d04-mfds-resolution.md)에 빈 행,
원료코드 매핑, 두 순번의 의미를 구분했다. 제안과 질문은 승인·전송 전 상태다. 실제 Source
자동 적재 조건이 정해졌다고 해석하지 않으며 기존 명시적 입력 검증을 유지한다.
