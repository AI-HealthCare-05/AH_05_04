# #166 D-03 Crosswalk 범위와 재개 조건

- 상태: 정현우가 PR #477 공개 리뷰에서 확인한 범위 결정. Crosswalk 구현 완료·Runtime 승인 아님.
- 확인일: 2026-09-13
- 구현 담당: 김지혜. Candidate·Identity·Runtime 의미 확인: 정현우. DB·migration 검토: 송은영.
- 공개 확인 근거: [PR #477 정현우 승인 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/477#pullrequestreview-5190458759), 검토 HEAD `c58f09686d3a72567793ee237abeb500ab04e710`.
- 최초 합의 이력: [D-03 Crosswalk 범위 답변](https://discord.com/channels/@me/1545029651477307442/1548584827912069172). 사용자 제공 원문 링크이며 DM 접근 권한이 필요하다. 접근 가능한 공개 확인 근거는 위 PR 리뷰를 사용한다.

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
