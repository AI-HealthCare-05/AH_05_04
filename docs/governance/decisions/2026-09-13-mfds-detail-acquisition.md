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

## 빈 주성분 행 처리 (2026-09-14 확인)

전체 상세 조회 126,825행 중 31,697행이 주요 성분 필드가 모두 비어 있다. 빈 행 3건을 공식 상세
화면과 대조한 결과 화면에는 성분이 존재했다. [대조 기록](../../testing/mfds-component-key-audit-166.md#빈-행-3건의-공식-상세-대조).
따라서 빈 값을 성분 없음으로 해석하지 않는다. 공급기관의 반환 조건 확인은 회신 시점을 알 수 없어
선행 조건으로 두지 않는다.

- 확인: 권가빈(제품 판단), 송은영(저장 경계), 남한솔(Frontend 표시). 2026-09-14.
- 빈 주성분 행은 `EMPTY_COMPONENT_FIELDS` 제외로 남기고 구성원을 만들지 않는다.
  전체 Catalog 차단 사유로는 쓰지 않으며 나머지 행으로 Catalog를 구성한다.
- `INVALID_COMPONENT_FIELDS`와 `CONFLICTING_OBSERVATION`은 원문 무결성 위반이므로 전체 차단을 유지한다.
- 제품은 Catalog와 검색·식별에 남는다. 검색 항목은 제품명·Alias로 만들어져 성분과 무관하다.
  관찰 행 없는 제품은 허용하고, 제품 없는 관찰 행은 orphan이므로 계속 거부한다.
- **구성원 0개는 성분 없음이나 금기 없음이 아니다. 성분 기반 안전성 검사는 이 상태를 판정 불가로
  다루어야 하며 통과로 해석하지 않는다.** 사용자 표시 상태값과 문구는 후속 Safety/Frontend 계약에서 정한다.
- 제외 건수와 사유는 Source Snapshot 단위 DB receipt에 남긴다. 상세 원문 위치와 source record key는
  private 조사 sidecar에 유지한다. manifest payload와 `is_complete`는 변경하지 않는다.
- 제품 단위 상태값은 이번 범위에서 제공하지 않는다. 집계 건수만으로 특정 제품의 표시 상태를 판단하지 않는다.

## 수집 계층 primary key 통과 판정 (2026-09-14 확인, #525)

Catalog 매핑에서 빈 행을 통과시켜도 수집 계층의 primary key 검사가 먼저 run을 `SCHEMA_DRIFT`로
끝내 실제 Snapshot이 생성되지 않았다. 빈 행을 `primary_key_null_count`에서 조용히 빼 같은
`receipt_version=1.1` 안에서 의미를 바꾸지 않는다.

- 확인: 송은영. 2026-09-14.
- 빈 행은 수집 단계에서 별도 집계한다(`excluded_empty_row_count`).
- 원본 수집 행 기준 null 통계는 감사·진단용으로 보존한다.
  통과 판정은 빈 행을 제외한 `enforced_primary_key_null_count`로 한다.
- 비어 있지 않은 행에서 `ITEM_SEQ`·`TAMT_SEQ`·`MTRAL_SN`이 비면 계속 `SCHEMA_DRIFT`로 차단한다.
- 빈 행 분류는 상세 Operation 계약에만 선언한다. 선언하지 않은 Operation의 판정은 바뀌지 않으며
  DUR·환자용 Endpoint의 기존 차단도 유지한다.
- Receipt 필드 의미와 통과 판정 기준이 바뀌므로 상세 Receipt는 `receipt_version` 1.2를 쓴다.
  기존 1.1 Receipt는 재발급 없이 계속 수용한다. 이 결정은 실제 수집 실행·승인이 아니다.

검토 대안: 제품 parser 재사용은 잘못된 키/checksum으로 배제했다. 새 Source DB 모델은 기존 저장 형태로
충분하므로 추가하지 않았다. nullable 관찰 키 Snapshot 및 파생 subset은 별도 공유 계약 변경이므로 구현하지 않았다.
유지보수 추가분은 상세 endpoint 후보·parser/수집 진입점·합성 회귀이며 API key·운영 승인 관리 경로는 확장하지 않는다.

[구현 계약과 실제 실행 전 조건](../../contracts/proposed/post-mvp-1/mfds-detail-acquisition-166.md)
