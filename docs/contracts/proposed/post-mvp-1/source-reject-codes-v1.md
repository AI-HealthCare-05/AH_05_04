# Source reject codes v1 — 구현 리뷰안

상태: proposed / 로컬 구현·관련 검증 완료 / 담당 리뷰 전. Target·Current 또는 공개 승인 아님.
작성·구현: 김지혜. Source/Parser 검토: 정현우. DB·무결성 리뷰: 송은영. 제품·Safety: 권가빈.
근거: 사용자가 제공한 `issue-165-reject-code-proposal.md` v2 및 2026-09-11 진행 지시.
현우님 의견은 반영된 기준이며, 은영님 사전 의견 없이 구현 후 PR 리뷰를 받는다.
이 지시는 구현 착수를 허용하며 담당자 승인이나 Production 활성화를 대신하지 않는다.

## 목록·판정

계약 버전은 `source-reject-codes@1`. 적용 범위는
`MFDS_PRODUCT_APPROVAL / MFDS_PRODUCT_APPROVAL_API / LIST_APPROVED_PRODUCTS` 하나다.

| 코드 | 조건 |
| --- | --- |
| ITEM_SEQ_REQUIRED | key 누락, null, 빈 문자열, 공백만 있는 문자열 |
| INVALID_ITEM_SEQ_TYPE | null 이외의 비문자열(숫자·boolean·배열·객체) |
| DUPLICATE_ITEM_SEQ | 모든 페이지를 통틀어 유효한 ITEM_SEQ 원문 문자열이 중복 |

필수값 → 타입 → 중복 순으로 2-pass 판정한다. 첫 오류에서 중단하지 않으며 중복 그룹 전체를 거부한다.
trim·강제 문자열 변환·dedupe를 하지 않고, 레코드당 대표 코드와 REJECTS Artifact를 하나만 기록한다.
위 세 코드는 필수 식별자 무결성 오류로 양수 Hard Limit에서도 FAILED / PARSER_VALIDATION_FAILED다.
Snapshot을 생성하거나 CURRENT를 변경하지 않는다. Hard Limit 계산 자체와 다른 실패 의미는 유지한다.
미등록 코드·미지원 계약 버전·적용 범위 밖 사용도 PARSER_VALIDATION_FAILED로 실패하며 입력값을 로그에 노출하지 않는다.
UNKNOWN/OTHER fallback은 없다. 이 경계는 별도 Decision 리뷰안과 함께 검토한다.

## 위치·원문·저장

`parser_location`은 `page[{page_number}].record[{record_index}]` 형식이다.
page_number는 검증된 응답 pageNo(1 이상), record_index는 정렬 이전 원본 배열의 0-based 위치다.
필드명·원문 식별자를 넣지 않는다. 문법뿐 아니라 페이지 하한과 실제 위치 결속을 검사한다.
동일 위치의 숫자 표기 차이(예: `page[01].record[00]`)로 중복 Artifact를 추가할 수 없다.

Run의 nullable `reject_code_contract_version`에 실행 전체의 불변 버전을 기록한다.
실패·거부 0건 실행에도 남기며 과거 NULL을 추정 보정하지 않는다. Artifact별 버전 중복 저장은 없다.
#436 canonical provenance는 checksum 이전 실패를 표현할 수 없어 버전 전용 필드를 사용한다.
새 REJECTS의 버전·Operation·코드는 파일 보존 전과 Repository 저장 경계에서 검증한다.
버전 없는 과거 Run은 감사 조회만 가능하며 새 계약을 만족한 실행으로 재사용하지 않는다.
Parser 출력 의미가 바뀌는 이번 실행 경로는 별도 parser version에 결속한다.
버전 변경은 기존 NO_CHANGE/충돌 비교의 parser version으로 구분한다.

원문은 기존 접근 통제 Artifact 저장소에만 보존한다. 임시 파일은 비공개로 만들고 즉시 정리한다.
DB 오류 요약·로그·Stream·DLQ에 원문이나 미등록 코드 입력을 넣지 않는다.
Artifact/DB 실패 시 성공을 반환하지 않으며 호출자 transaction rollback과 기존 orphan reconciliation을 유지한다.
2-pass는 기존 전체 수집 결과의 레코드를 참조하고 원문 복사 없이 위치와 식별자별 개수를 보관한다.
추가 메모리는 레코드 수에 선형이며 Source client의 기존 페이지/응답 크기 제한을 유지한다.

## 버전·승격·범위

문구 수정만이면 버전 유지. 코드 추가·삭제·범위·의미·우선순위·실패 처리 변경이면 새 정수 버전과 Decision.
과거 코드 의미 재사용과 새 버전 자동 허용은 금지한다. 문서와 코드 목록은 계약 테스트로 대조한다.
proposed → targets는 담당 계약 검토 이후, current는 구현·migration·검증·담당 승인 병합 이후다.
이 문서는 한 상태 경로에만 존재하며 #165 전체 Close, #166 D-02, 사용자 입력 정규화, Runtime 활성화는 범위 밖이다.

Python Service/Repository에서 업무 로직·무결성을 관리한다. Trigger·RLS·업무 DB 함수/프로시저의
신규 추가·복원은 금지한다. 일반 제약·transaction·최소 권한과 기존 migration 이력은 보존한다.
