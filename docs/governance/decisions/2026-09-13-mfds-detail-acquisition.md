# D-04 상세 수집 생산 경계

- 상태: Proposed / #166 수집기 구현 PR에서 검토. 담당자 승인·실제 수집 완료 선언 아님.
- 기존 근거: [D-04 관찰 출처 결정](2026-09-13-component-observation-handoff.md),
  [MFDS 공식 명세](https://www.data.go.kr/data/15095677/openapi.do).
- 구현 김지혜 / Source 저장·무결성 담당 리뷰 송은영. 기존 MFDS 의미·Candidate 범위는 정현우의 #477 검토를 유지.

제품 목록의 ITEM_SEQ 단일 키·제품 checksum으로 주성분 상세를 저장하면 반복 성분을 보존할 수 없다.
따라서 별도 상세 Operation과 parser를 추가하고, 기존 Source 저장 DTO/lifecycle은 같은 형태로 공유한다.
제품 공개 진입점은 유지한다. 새 DB 구조나 운영 자동 실행은 추가하지 않는다.

빈 키 행을 채우거나 버리는 대신 원문과 FAILED Run을 보존한다. 전체 상세 배열의 키·무결성 검증을
통과한 경우에만 PENDING Snapshot을 생성한다. 제한 적재 및 subset에 전체 Receipt를 붙이는 방식은 금지한다.
공식 성분 순서·전역 Identity·Snapshot 동일시점성을 이번 내부 구현 규칙으로 확정하지 않는다.

검토 대안: 제품 parser 재사용은 잘못된 키/checksum으로 배제했다. 새 Source DB 모델은 기존 저장 형태로
충분하므로 추가하지 않았다. nullable 관찰 키 Snapshot 및 파생 subset은 별도 공유 계약 변경이므로 구현하지 않았다.
유지보수 추가분은 상세 endpoint 후보·parser/수집 진입점·합성 회귀이며 API key·운영 승인 관리 경로는 확장하지 않는다.

[구현 계약과 실제 실행 전 조건](../../contracts/proposed/post-mvp-1/mfds-detail-acquisition-166.md)
