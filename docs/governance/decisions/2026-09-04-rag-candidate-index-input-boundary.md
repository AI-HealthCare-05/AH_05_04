# Product Decision: RAG-07A Candidate Index 입력 경계 보완

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-167-20260904` |
| 상태 | Review pending · PR #260 병합 전 승인 필요 |
| 제안일 | 2026-09-04 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 김지혜 (`@Jye-rookie`) — MFDS Catalog·Parser 인계 |
| 교차 리뷰 | 송은영 (`@phina-io`) — Backend·Source·RAG-07B 경계, 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation provenance |
| 추적 Issue·PR | [#167](https://github.com/AI-HealthCare-05/AH_05_04/issues/167) · [PR #260](https://github.com/AI-HealthCare-05/AH_05_04/pull/260) |
| 상위 결정 | [`PD-125-20260831`](./2026-08-31-rag-p0-contract-freeze.md) |

## 결정 제안

이 Decision은 `PD-125-20260831`의 공식 Product Identity, 승인 Source Snapshot, 결정적 Candidate Index와
fail-closed 원칙을 RAG-06→RAG-07A→RAG-07B 내부 인계 타입에 구체화한다. 환자 API·DTO, DB schema,
Candidate Resolver 판정, 외부 Source 승인 또는 `PUBLIC_TRACK_F`를 변경하지 않는다.

1. Catalog와 Candidate Index manifest는 Source Snapshot ID와 Source version을 독립 배열로 저장하지
   않고 `(snapshot_id, source_version)`이 결속된 `CandidateCatalogSourceRef` 목록으로 보존한다.
2. Candidate 구성원은 Product·Search Entry·nullable Alias Source Snapshot을 각각 보존한다.
3. Catalog·BuildConfig·Query의 문자열, enum, bool과 정수 타입은 비교·정렬·hash 전에 검증한다. 타입
   오염은 예외나 truthy 성공이 아니라 기존 typed failure로 닫는다.
4. 같은 Search Entry reference의 완전히 동일한 반복만 병합한다. 다른 Identity·payload 재사용은
   `MEMBER_CONFLICT`다.
5. HYBRID ANN key/value는 중복·공백을 허용하지 않고 key 기준으로 canonical 정렬한다. 입력 tuple
   순서는 manifest·configuration hash를 바꾸지 않는다.
6. Embedding과 Search port는 최상위 container뿐 아니라 vector tuple, Product Identity, enum, 필수
   문자열과 숫자 타입까지 검증한다. 잘못된 중첩 payload는 partial 결과 없이 typed failure로 닫는다.

## 오류와 공개 경계

이 보완은 `CATALOG_REQUIRED_FIELD_INVALID`, `CATALOG_SOURCE_BINDING_INVALID`,
`REFERENTIAL_INTEGRITY_INVALID`, `MEMBER_CONFLICT`, `BUILD_CONFIG_INVALID`,
`EMBEDDING_OUTPUT_INVALID`, `QUERY_INVALID`, `PORT_FAILURE`, `HIT_PROVENANCE_MISMATCH`의 내부 의미를
명시한다. 실패 detail에는 Source raw row, 검색 원문, Alias text, vector 또는 credential을 포함하지
않는다. Candidate score·vector·raw hit는 환자 DTO나 의료 Evidence로 투영하지 않는다.

## 승인과 적용 조건

이 문서는 현재 `Review pending`이며 그 자체로 Approved Target 변경이나 Production 활성화를 승인하지
않는다. PR #260의 최신 HEAD에서 책임·교차 리뷰어가 각 범위를 승인하고 자동 검증이 통과해야 이
Decision과 Target 보완을 함께 병합할 수 있다. 승인 전에는 기존 `PD-125-20260831`보다 넓은 의미를
추정하거나 RAG-06/RAG-07B 통합 완료를 주장하지 않는다.
