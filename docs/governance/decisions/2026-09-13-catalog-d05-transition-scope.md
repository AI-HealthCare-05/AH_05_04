# #166 D-05 hash 전환 보류 범위와 재개 조건

- 상태: **현우님 답변 반영 기록 / 문서 PR 검토 대상**. 신규 hash 구현·Runtime 활성화 승인 아님.
- 기준: develop `f10ca016` (#477 병합 포함), 2026-09-13 사용자 제공 답변 캡처(오후 10:19 표시).
- 구현 담당: 김지혜. 이번 문서 PR 담당 리뷰어: 정현우 1명.
- 이후 DB·migration 변경이 생기면 송은영 담당 범위로 검토한다. 이 기록은 DB 변경 승인이 아니다.

## 근거와 승인 범위

- 기존 검토 반영본: 사용자 제공 `issue-166-d05-hash-calculation-draft-v2.md` (2026-09-08).
  원문은 저장소에 복제하지 않는다. 식별용 SHA-256과 코드 대조는 [선행 조건 점검](../../designs/jye-rookie/issue-166-d05-readiness.md)에 기록한다.
- 기존 공개 근거: [#166 현우님 댓글 D-05](https://github.com/AI-HealthCare-05/AH_05_04/issues/166#issuecomment-5594696171).
  현행 v2 계산·저장과 미확정 Authority/Runtime hash를 구분하라는 요청이다.
- 최신 답변: [정현우 D-05 원문 메시지](https://discord.com/channels/@me/1545029651477307442/1548684514866495550).
  사용자 제공 캡처에서 내용을 확인하고 사용자 제공 원문 링크를 연결했다. DM 접근 권한이 필요하며,
  이 작업에서 Discord 원문을 직접 조회한 것은 아니다. 과거 #477 리뷰를 이번 답변의 공개 승인 근거로
  대신 연결하지 않는다. 이번 문서 PR의 검토·승인 증빙은 해당 PR에서 별도로 확인한다.

## 답변 반영 결과

| 항목 | 정리된 범위 |
| --- | --- |
| 전체 D-05 전환 | 현재 Product MFDS_ITEM_SEQ 중심 P0와 Crosswalk 제외 경계를 고려해 후속으로 유지 |
| Product Identity 한정 projection | 실제 필요가 있다면 별도 Identifier Set 없는 축소 계약을 검토할 수 있음. 착수·활성화 확정 아님 |
| 기존 member_content_hash | 내부 참조·Snapshot·envelope를 포함하므로 신규 Entry 본문 hash로 재사용 금지 |
| 본문·provenance 분리 | 제안한 본문 필드와 별도 binding 방향에 동의. 계산 spec·fixture 전체가 확정됐다는 뜻은 아님 |
| semantic key 중복 | 상충 본문 거부. 같은 본문은 의미 구성원 하나로 두되 전체 출처 binding 보존. 기존 Product Identity 중복 거부를 변경한다면 별도 계약 변경 명시 |
| D-02 | 미확정 유지. 분리된 content hash 규칙 검토는 가능하나 임의 실행 ID/FK 생성 금지 |
| Runtime manifest | 총량·관찰 정보의 Runtime 소비 규칙이 없으므로 구성원 범위와 실제 연결 모두 보류 |
| 관찰 v3 | medication-catalog-v3는 Component 관찰 export이며 D-05 projection이 아님 |
| 기존 경로 | v2·관찰 v3 envelope 유지. 새 hash는 별도 종류·계약 버전, 기존 hash 대입·자동 변환 금지 |

본문 필드 제안은 Product Identity·Entry 유형·정규화 문자열·normalization version과 별도 본문 hash를
결속하고, 본문에 표시 문자열·제품명·함량 표시·제형·제조사명을 포함하는 방향이다.
최신 답변은 이 방향과 출처 분리에 대한 동의다. 구체적 canonical schema·버전명·독립 예상 digest를
발급한 것이 아니며, 현행 Candidate member/key 생성·중복 거부 규칙은 변경하지 않는다.

## 지금 유지할 구현

- 기존 export checksum과 envelope hash의 의미·bytes 재검증을 유지한다.
- Catalog의 현재 hash 종류·저장 제약·Candidate 입력·Runtime 기존 Catalog binding을 변경하지 않는다.
- 신규 hash 계산 함수·placeholder 값·projection golden·Runtime 전용 migration을 추가하지 않는다.
- Crosswalk P0 제외와 독립 Authority Alias Set 미대체 경계는 [D-03 기록](2026-09-13-catalog-crosswalk-scope.md)을 따른다.
- 저장 성공이나 문서 리뷰를 실제 승인·철회·감사 저장소 완료, D-05 완료 또는 #166 종료로 해석하지 않는다.

## 재개 조건과 순서

1. 실제 소비 기능을 명시해 전체 전환 또는 축소 projection의 필요성과 범위를 확인한다.
2. 전체 전환이면 기존 네 선행 조건을 준비한다. 축소안이면 승인 Identifier 집합 조건의 변경을 새 계약에 명시한다.
3. Entry 본문·allowlist·semantic key·중복·출처 binding·canonicalization·버전을 확정한다.
4. Runtime 전환은 별도로 총량·관찰 등 소비 규칙과 구성원 범위를 확보한다. D-02 의존 항목은 미확정으로 유지한다.
5. 생산자·Candidate·Runtime 각각의 재계산 책임, 구버전 수용/거부 및 명시적 전환 경계를 정한다.
6. 독립 기대 bytes/digest와 실패 사례를 갖춘 golden fixture를 확정한다.
7. 새 hash 종류/target CHECK·Set/spec 식별 범위·Runtime 참조의 DB 변경안을 작성해 은영님 검토에 연결한다.

현재는 1번의 실제 필요가 확인되지 않았으므로 2~7의 구현을 먼저 진행하지 않는다.
사전 코드 조사와 후보·검증 사례는 보존하되 실행 가능한 승인 계약으로 승격하지 않는다.

## 검증 이력

같은 코드 HEAD에서 기존 hash·복원·Component 순서 테스트 39건이 통과했다.
이는 [선행 조건 점검 문서](../../designs/jye-rookie/issue-166-d05-readiness.md)의 기존 코드 검증 이력이며,
이번 문서 변경 후 전체 CI·신규 hash 계약·운영 승인 증빙이 아니다.
