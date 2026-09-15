# D-04 상세 수집 생산 경계

- 상태: Proposed 유지(계약 승격 없음). #543·#547 병합으로 완료된 실측·QNT 증빙 범위는 아래 「2026-09-15 완료 기록」에서 구분한다. Source 승인·Snapshot DB 저장·Catalog 인계 완료 선언은 아니다.
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
- `MISSING_COMPONENT_QUANTITY`, `INVALID_COMPONENT_KEY_FIELDS`, `CONFLICTING_OBSERVATION`은 전체 차단을 유지한다.
- 제품은 Catalog와 검색·식별에 남는다. 검색 항목은 제품명·Alias로 만들어져 성분과 무관하다.
  관찰 행 없는 제품은 허용하고, 제품 없는 관찰 행은 orphan이므로 계속 거부한다.
- **구성원 0개는 성분 없음이나 금기 없음이 아니다. 성분 기반 안전성 검사는 이 상태를 판정 불가로
  다루어야 하며 통과로 해석하지 않는다.** 사용자 표시 상태값과 문구는 후속 Safety/Frontend 계약에서 정한다.
- 제외 건수와 사유는 Source Snapshot 단위 DB receipt에 남긴다. 상세 원문 위치와 source record key는
  private 조사 sidecar에 유지한다. manifest payload와 `is_complete`는 변경하지 않는다.
- 제품 단위 상태값은 이번 범위에서 제공하지 않는다. 집계 건수만으로 특정 제품의 표시 상태를 판단하지 않는다.

## 분량 누락 행 처리 (2026-09-14 확인)

전체 실측(1,269페이지·126,823행)에서 빈 행 31,702건과 별개로, 키·성분코드·단위는 있는데
분량만 비어 있는 행이 약 29,931건 확인됐다. 두 품목을 공식 상세 화면과 대조한 결과
**화면에는 분량이 표시된다.**

| 품목 | API | 공식 화면 |
| --- | --- | --- |
| 202402384 비파인맥스정 | 6건 모두 `QNT` 없음 | 100·30·5·5·0.5·0.5 밀리그램 |
| 201604069 칼리크정 | `QNT` 없음, 단위만 `아이.유` | 50 아이.유 |

빈 행과 같이 제공자가 값을 비워 반환하는 경우이며 분량이 없는 것이 아니다.

- 확인: 권가빈(제품 판단), 송은영(저장 경계). 2026-09-14.
- **`QNT`를 nullable로 풀어 정상 Component로 저장하지 않는다.** 실제로는 분량이 있는 약을
  빈 값으로 저장하게 되어 성분 정보를 왜곡하고 이후 안전성 판단에서 잘못 쓰일 수 있다.
- **전체 Catalog build 차단을 유지한다.** 분량 누락 행만 제외하고 나머지로 Catalog를 구성하는
  방식은 현재 계약으로 허용하지 않는다.
- 감사·후속 복구를 위해 차단 사유를 구분한다. 분량만 누락된 행은 `MISSING_COMPONENT_QUANTITY`,
  identity·join 필드가 손상된 행은 `INVALID_COMPONENT_KEY_FIELDS`로 기록한다.
  **사유 분리는 부분 적재 허용이 아니며 두 사유 모두 차단을 유지한다.**
- 일부 성분만 저장된 제품은 구성원 0개보다 위험할 수 있다. 0개는 판정 불가로 다루기 쉽지만
  일부만 있으면 그것이 전체 성분으로 오해될 수 있다.
- 부분 집합 Catalog를 허용하려면 제외 row·product 수, 제품별 불완전 상태, 성분 목록을 완전한
  것으로 소비하지 못하게 하는 차단 기준, Candidate·Runtime·안전성 판단에서의 제외 방식,
  전체 Source Receipt와 구분되는 별도 receipt/version을 갖춘 별도 계약이 필요하다.
- 이번 배포 범위는 실측 Receipt와 증빙 정리까지이며 Catalog 인계는 후속으로 둔다.
  공급기관 회신 전에는 실제 Catalog 인계·활성화를 진행하지 않는다.

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

## 2026-09-15 완료 기록 — 이번 배포의 QNT 대응

이 절은 기존 구현과 2026-09-14 팀 답변을 연결하는 상태 기록이다. 새 적재 방식·차단 예외나
계약 승격을 추가하지 않는다. 아래 완료 범위만으로 상위 [#166](https://github.com/AI-HealthCare-05/AH_05_04/issues/166)을 종료하지 않는다.

### 병합된 범위와 증거

| 범위 | 완료 근거 |
| --- | --- |
| 상세 Endpoint 전체 실측·Receipt 1.2 | [#543](https://github.com/AI-HealthCare-05/AH_05_04/pull/543), `16b6fd2385c3af3d3f07a7657270e6b341e19f02`. 1,269페이지·126,823행, 빈 행 31,702건. 원본 null 통계와 통과 판정 분리 |
| QNT 누락·identity 손상 구분 및 inspection 사유별 집계 | [#547](https://github.com/AI-HealthCare-05/AH_05_04/pull/547), `fba61d84`. 공식 화면 대조 결과와 두 차단 reason·회귀 테스트 반영 |
| Catalog 전체 차단 유지 | `MISSING_COMPONENT_QUANTITY`·`INVALID_COMPONENT_KEY_FIELDS` 모두 `_BLOCKING_EXCLUSION_REASONS`에 포함. `eligible_for_mapping=false`이면 Loader가 build를 반환하지 않음 |

- 실측 정본: [Endpoint Receipt](../../validation/rag/endpoints/LIST_PRODUCT_COMPONENT_DETAILS.json).
- 차단·집계 구현: [Component inspection](../../../ai_worker/tasks/rag/catalog/mfds_component.py),
  [Catalog Loader](../../../ai_worker/tasks/rag/catalog/mfds_loader.py).
- 회귀 근거: [Component 테스트](../../../ai_worker/tests/rag/catalog/test_mfds_component.py)의
  `test_quantity_only_gap_is_separated_from_key_damage_but_still_blocks`,
  `test_identity_field_damage_is_not_reported_as_a_quantity_gap`,
  `test_exclusion_counts_are_reported_by_reason`.
- 과거 테스트 실행 결과는 #547 본문에 기록돼 있다. 이번 문서 변경에서 재실측·테스트 재실행·배포를 수행한 것은 아니다.

### 최종 팀 답변과 Snapshot 경계

기록 근거는 김지혜가 공유한 2026-09-14 팀 대화다. 권가빈은 20:48에 이번 배포를 실측 Receipt·증빙
정리까지로 제한하고 Catalog 인계를 후속으로 두는 데 동의했다. 송은영은 23:24에 같은 범위와
아래 저장 경계를 확인했다. 개인 대화 링크·스크린샷 원본 대신 합의 내용을 여기에 요약한다.

- QNT를 nullable로 저장하거나 누락 행만 제외해 부분 Catalog를 구성하지 않는다.
- 일부 성분만 남은 제품을 완전한 성분 목록으로 소비하지 않는다.
- Snapshot은 기존 Source 수집·검증 증적의 계약상 가능한 범위에 한정한다.
  Source 응답 수집과 Catalog 인계 차단 사유의 증거이며, 승인 Catalog·Candidate·Runtime 입력으로 승격하지 않는다.
- 이 경계 합의가 실제 Snapshot 저장 성공을 뜻하지 않는다.
  [현행 상세 수집기](../../../ai_worker/tasks/rag/source_ingestion/mfds_detail.py)는 inspection 부적격 또는 중복 시
  Snapshot 생산을 거부한다. 이 거부를 우회하는 별도 저장 경로는 이번 문서에서 만들지 않는다.

### 완료와 후속 구분

**이번 배포의 실측 Receipt·inspection 증빙/집계·reason 세분화는 완료다.** Plan B 전환 자체로
추가 QNT 구현이나 Catalog 차단 해제가 발생하지 않는다.

- 실제 Source Snapshot DB 저장·Catalog 인계·활성화는 완료로 표시하지 않는다.
- 부분 집합 Catalog, 제품별 불완전 상태, Candidate·Runtime·안전성 판단의 소비 제한은 후속 계약으로 남긴다.
- 기존 [#526](https://github.com/AI-HealthCare-05/AH_05_04/issues/526) 승인 저장소,
  [#527](https://github.com/AI-HealthCare-05/AH_05_04/issues/527) Runtime hash,
  [#528](https://github.com/AI-HealthCare-05/AH_05_04/issues/528) 실행 모델,
  [#529](https://github.com/AI-HealthCare-05/AH_05_04/issues/529) Component 자연키 추적은 유지한다.
  이 이슈들의 완료가 QNT 차단 해제나 부분 Catalog 계약 승인을 대신하지 않는다.
- #526의 Plan B 착수 순서는 실제 적격 데이터와 소비 경로를 확인해 정한다. 이번 기록에서 Catalog 인계를 약속하지 않는다.
