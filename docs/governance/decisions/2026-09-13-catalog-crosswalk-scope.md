# #166 D-03 Catalog Set·Authority Alias Set 관계와 Crosswalk 범위

- 상태: 정현우가 PR #477 공개 리뷰에서 확인한 범위 결정. Crosswalk 구현 완료·Runtime 승인 아님.
- 확인일: 2026-09-13
- 구현 담당: 김지혜. Candidate·Identity·Runtime 의미 확인: 정현우. DB·migration 검토: 송은영.
- 공개 확인 근거: [PR #477 정현우 승인 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/477#pullrequestreview-5190458759), 검토 HEAD `c58f09686d3a72567793ee237abeb500ab04e710`.
- 최초 합의 이력: [D-03 Crosswalk 범위 답변](https://discord.com/channels/@me/1545029651477307442/1548584827912069172). 사용자 제공 원문 링크이며 DM 접근 권한이 필요하다. 접근 가능한 공개 확인 근거는 위 PR 리뷰를 사용한다.

## Catalog Set·Authority Alias Set 관계 보충 (2026-09-13)

- 기록 요청 근거: [#166 현우님 댓글의 D-03 다섯 항목](https://github.com/AI-HealthCare-05/AH_05_04/issues/166#issuecomment-5594696171).
- 코드 대조 기준: develop `f10ca016` (#477 병합 포함).
- 이 절은 기존 기록에서 빠진 관계 설명을 코드에 근거해 보충하는 문서 리뷰 대상이다.
  위 #477 승인을 이 신규 보충의 승인이나 Authority 대체 승인으로 확대하지 않는다.

| 별도 기록 요청 | 현재 구현·처리 범위 |
| --- | --- |
| 1. rag_catalog_set은 현재 v2 저장·재현의 구성 단위인가 | 그렇다. manifest와 Source/member/hash를 결속하는 일반 Catalog 구성 단위다. #477 이후 관찰 자료가 있는 v3 export도 같은 저장 구조를 사용한다. v2 전용으로 한정하거나 normalization 실행·Publication 승인 단위로 해석하지 않는다. |
| 2. Authority Alias Set을 대체하는가 | 아니다. Alias 행은 일반 Catalog Set의 ALIAS member로 포함된다. 독립 alias_set/alias_set_member 엔티티를 구현한 것이 아니며 Authority Alias Set의 버전·승인·Runtime 계약을 대체했다고 주장하지 않는다. |
| 3. Crosswalk Set을 현재 P0에 포함하는가 | 아래 기존 범위 결정대로 제외한다. Alias 포함 관계와 별개다. |
| 4. 승인 Crosswalk 입력이 없을 때 빈 Set을 허용하는가 | Crosswalk를 나타내는 빈 READY Set을 만들지 않는다. 이름·공식 코드 복제로 추론한 행도 만들지 않는다. 일반 Catalog의 구성 규칙에 대한 새 변경은 아니다. |
| 5. 후속 Issue·Runtime 연결 조건은 무엇인가 | 현재 #166에 보류 범위와 아래 재개 조건을 남긴다. 이 문서 작업에서 별도 후속 Issue를 생성하지 않았으며 생성된 것으로 표시하지 않는다. 실제 소비 경로·공식 입력·승인 주체·Set/Runtime 참조·DB 및 테스트 계약을 갖춰 별도 Issue/계약으로 재개한다. |

### 코드에서 확인한 포함 관계

[rag_catalog.py](../../../backend/app/models/rag_catalog.py)의 `RagCatalogMemberKind`는
`PRODUCT / INGREDIENT / COMPONENT / ALIAS / SEARCH_ENTRY`다.
`RagCatalogSet`에는 Alias Set/Crosswalk Set 등을 구분하는 Set 종류 컬럼이 없다.
`RagCatalogSetMember`의 `member_kind=ALIAS` 행은 `alias_id`로
`rag_medication_alias.id`를 참조하며, 종류별 대상 CHECK로 다른 구성원 FK와 구분한다.
즉 **Catalog Set이 Alias member를 포함하는 관계**이지, **Catalog Set이 Authority Alias Set을
대체하는 관계**가 아니다. `member_kind`는 구성원의 종류이며 Set 자체의 종류가 아니다.

D-03a의 안정 Identity 대상·Alias 상태/출처 전환과 Catalog 저장·복원은 이 포함 구조에 구현돼 있다.
이를 독립 Authority Alias Set 구현 완료나 해당 목표 폐기 결정으로 간주하지 않는다.
독립 Alias Set과의 관계를 추가 구현/전환하려면 실제 소비·승인·불변 구성·Runtime 참조 범위를
별도 계약과 증빙으로 확인해야 한다. 이번 보충은 테이블·enum·FK·migration을 변경하지 않는다.

## 현재 결정

현재 P0는 Product `MFDS_ITEM_SEQ`, Ingredient `MFDS_INGREDIENT_CODE`를 사용하며 별도
코드 체계 간 Crosswalk 소비 경로가 없다. D-03 Crosswalk는 현재 P0 및 #166의 즉시 구현
범위에서 제외한다. Alias D-03a 전환과 구분하고, Crosswalk 구현 완료로 표시하지 않는다.

- HIRA 데이터와 보험코드 입력·Exact 검색은 비활성으로 유지한다.
- 승인된 입력 없이 이름·기존 코드로 매핑을 추론하거나 빈 READY Set을 만들지 않는다.
- `rag_catalog_set`을 Crosswalk Set으로 취급하거나 임의 확장하지 않는다.
- 향후 승인 MFDS Source의 보험 Identifier 경로, 일반 Identity Crosswalk,
  D-04의 MTRAL_CODE → MFDS_INGREDIENT_CODE 연결은 각각 별도 범위다.
- 이 결정으로 D-02·D-04·D-05·실제 승인/철회/감사 저장소의 남은 작업을 종료하지 않는다.

## 별도 계약·이슈로 재개할 조건

1. 실제 소비 기능과 연결할 코드 체계·entity type을 명시한다.
2. 공식 매핑 자료의 제공 기관·버전·적용 범위를 확보한다.
3. Source 사용 승인과 매핑 검토 주체를 명시한다.
4. Crosswalk Set/member의 provenance·불변성·Runtime 결속을 정의한다.
5. DB migration과 Contract/Integration Test 범위를 함께 정한다.

현재 필요가 없다는 결정이지, 데이터만 들어오면 자동 활성화할 수 있다는 계약이 아니다.
정현우는 Candidate 입력·Identity 의미·Runtime 소비 경계를 확인하며,
DB 구조와 migration은 송은영 검토 범위로 구분한다. 구현 일정·P0 확대가 필요할 때 PM과 조율한다.

## #166 반영 문구

> D-03 Crosswalk는 현우님 확인 결과 현재 P0 소비 경로가 없어 즉시 구현 범위에서 제외합니다.
> 승인 입력 없는 매핑 추론·빈 READY Set 생성·기존 Catalog Set의 Crosswalk 전용 전환은 하지 않습니다.
> 실제 소비 기능·공식 매핑 자료·Source 승인·검토 주체·Set/Runtime 계약·DB 및 테스트 범위가
> 확보되면 별도 계약과 이슈로 재개합니다. Crosswalk 구현 완료를 의미하지 않습니다.

GitHub Issue 본문·댓글은 이번 로컬 기록으로 자동 수정하지 않았다.
