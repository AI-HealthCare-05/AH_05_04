# #166 D-04 Component 순서 충돌과 자연키 전환 검토

상태: 아래 초기 검토 이력 이후 occurrence 전환안을 구현 중. 최신 기준은 마지막 보충과 연결된 결정안을 따른다.
최초 기준: develop `8010dfc`. #454 병합 후 `7f6cc85`를 충돌 없이 반영했다. 구현 김지혜. PR 책임 리뷰어 정현우 1명 (사용자 지정). Component/Candidate 의미 검토도 정현우.
기존 담당 경계를 기록한 것이며 새 리뷰 요청을 전송하거나 승인을 받았다는 뜻은 아니다.

## 근거와 구분

MFDS API 연결이 없는 상태가 아니다. `source_client/endpoints.py`는 제품 허가 조회
`getDrugPrdtPrmsnInq07`을 사용하고 제품 기본키를 `ITEM_SEQ`로 지정한다.
`docs/validation/rag/endpoints/README.md`에는 2026-09-05 제품 42,989건 전체 페이지
실측과 제품 키 중복 0건이 기록돼 있다. 이는 이번 작업의 새 실측이 아니다.

공식 제품 허가정보 설명은 품목·주성분 정보를 제공한다고 명시한다:
https://www.data.go.kr/data/15095677/openapi.do (2026-09-11 확인).
이 설명만으로 성분별 공식 코드·원본 행 키·반복 성분 순서 규칙까지 확정하지 않는다.

현재 `source_ingestion/parse.py`는 검증된 제품 레코드를 보존한다.
Catalog의 `CatalogComponentInput`은 공식 Product/Ingredient code와 명시적인
`component_order`를 받는다. 현재 호출처와 fixture에서 실제 MFDS 응답을 이 입력으로
변환하는 성분별 매핑 근거는 확인하지 못했다. `synthetic_components.json`은 합성 자료다.
사용자는 별도 명세·샘플 없이 저장소 근거로 진행하도록 확인했다.

## 재현한 차이

| 사례 | 기존 v2 동작 | 이번 처리 |
| --- | --- | --- |
| 같은 제품, 다른 성분, 같은 순서 | 자연키가 달라 검증 통과 | MEMBER_CONFLICT로 거부 |
| 다른 제품의 순서 1 | 서로 다른 제품 범위 | 계속 허용 |
| 같은 성분·role, 다른 순서·함량·방출형 | 두 내용은 보존하지만 같은 component_ref로 충돌 | 기존 거부 유지; 병합하거나 손실시키지 않음 |
| 입력 배열만 뒤집기 | 명시적인 order 유지 | 동일 JSONL·manifest·hash 재현 |
| 정확히 같은 행 재등장 | 행 단위 dedupe | 기존 동작 유지 |

## 이번 구현 결정안

Python 구성원 검증에 `(product_ref, component_order)` 충돌 검사를 추가한다.
기존 MEMBER_CONFLICT/REJECTED를 사용하고 공개 오류 enum을 신설하지 않는다.
Service는 승인 조회·Export·DB 저장 전에 거부한다. DB adapter의 준비 단계도 같은
검증을 재실행해 이전 검증 결과를 전달한 경우에도 쓰기 전에 차단한다.

Component order는 입력의 양의 정수를 그대로 보존한다. 배열 index로 재번호를 붙이지
않으며 1부터 연속해야 한다는 새 조건도 만들지 않는다. 원본 키를 만들거나 Component
hash를 source_record_key로 이름만 바꾸지 않는다. 정상 입력의 v2 bytes/hash 계약은 유지한다.
이는 기존 v2의 허용 입력을 좁히는 검증 변경이므로 담당 리뷰 대상으로 명시한다.

## 아직 구현하지 않는 자연키 전환

현재 DB는 `(product_id, ingredient_id, component_role)` UNIQUE이고 실제 순서 컬럼은
`display_order`다. Worker의 component_ref도 product/ingredient/role로 계산한다.
따라서 DB UNIQUE만 `(product_id, display_order)`로 바꾸면 반복 성분 참조 충돌이 남는다.

다음 근거와 계약이 함께 정렬되어야 전체 전환할 수 있다.

1. MFDS 상세 응답의 공식 Ingredient 코드, 함량·단위·방출형 필드 경로
2. 반복 성분을 구분하는 안정적인 순서 또는 원본 키와 재수집 시 안정성
3. component_ref 변경 여부와 기존 v2 Catalog/Set/member/hash 호환 정책
4. 기존 DB의 제품별 순서 충돌 사전 검사 및 실패 시 무보정 중단 정책
5. D-02 실행 범위와 현행 Snapshot별 Product row 범위를 혼동하지 않는 UNIQUE/FK 범위

DB migration·기존 자연키 제거·새 run/원본 키 도입은 이번 방어 보완에 포함하지 않는다.
기존 relationship은 Loader가 명시적 FK INSERT를 쓰므로 편의상 변경하지 않는다.

## 물리 전환 전 읽기 전용 검사안

기존 DB 전환 검토 시 아래 조회로 제품별 순서 충돌을 먼저 확인한다. 이번 작업에서
운영 DB에 실행하지 않았다. 충돌이 발견돼도 자동 삭제·재번호 부여하지 않는다.

```sql
SELECT product_id, display_order, COUNT(*) AS component_count
FROM rag_medication_product_component
GROUP BY product_id, display_order
HAVING COUNT(*) > 1;
```

이 조회는 현재 저장된 행의 순서 충돌만 확인한다. 기존 자연키가 막았던 반복 성분의
누락 여부나 MFDS 원본 키의 안정성은 입증하지 않는다.

## #454와의 파일 경계

공용 Worker config/runtime assembly·README·Compose·계약 목차는 수정하지 않는다.
이미 목차에 등록된 Catalog DB 계약에 본 변경안을 연결한다. 현재 #454 병합 내용을 반영했으며 공용 파일 변경이 필요한 대기 항목은 없다. RLS·Trigger·업무용 DB 함수는 추가하지 않는다.

## 공식 상세 명세 확인 (2026-09-11 추가)

공식 페이지에 내장된 Swagger를 확인했다. 현재 수집 Operation은 제품 목록 조회
`getDrugPrdtPrmsnInq07`이며 `ITEM_INGR_NAME`·`ITEM_INGR_CNT`를 제공한다.
별도 `getDrugPrdtMcpnDtlInq07`(제품 주성분 상세정보)에는 다음 필드가 문서화돼 있다.

| 필드 | 공식 명세상 의미 |
| --- | --- |
| ITEM_SEQ | 품목기준코드 |
| MTRAL_CODE | 원료코드 |
| MTRAL_SN | 일련번호 |
| TAMT_SEQ | 총량일련번호 |
| QNT | 분량 |
| INGD_UNIT_CD | 분량단위정보 |
| CPNT_CTNT_CONT | 세부구성항목 |

`ITEM_SEQ + TAMT_SEQ + MTRAL_SN`은 검토할 후보키이며 아직 확정 원본 키가 아니다.
`MTRAL_CODE`가 현재 Catalog의 `MFDS_INGREDIENT_CODE`와 같은 코드 체계인지도 확인해야 한다.
자료가 존재하지 않는다고 단정하지 않는다. 실제 응답의 타입·누락·중복 및 반복 조회와
API 접근 가능 여부를 확인한 뒤 전환 규칙에 반영한다. 문자열 순번의 숫자 변환이나
API 배열 index를 이용한 order 생성은 아직 구현하지 않는다.

키는 사용자가 별도로 보관한다. env 파일·채팅·저장소에 입력하도록 요구하지 않는다.
`scripts/rag/probe_mfds_component_fields.py --live`는 터미널의 숨김 입력으로 Decoding 키를
받아 첫 페이지 최대 50건을 두 번 조회한다. redirect와 환경 proxy를 사용하지 않으며,
응답 크기·timeout을 제한하고 인증 URL·키·응답 원문·Provider 오류 문구를 출력하지 않는다.
출력은 필드 통계·후보키 중복 통계·비교용 sample hash뿐이다. 명시적 --live가 없으면
외부 호출을 하지 않는다. 결과는 SAMPLED_NOT_VERIFIED이며 완료 Endpoint Receipt나
전체 고유성·공식 코드 승인 증빙으로 사용할 수 없다.

## 샘플 수령 후 전환안 (2026-09-11)

사용자가 `2026-09-11T09:35:20.046050+00:00`에 실행한 통계를 제공했다.
operation `getDrugPrdtMcpnDtlInq07`, 범위 `pageNo=1,numOfRows=50,two_requests`.
두 응답은 각각 50행이며 순서 무관 SHA-256이 같다:
`60694a235219eef63040045565d1aff739b6db258ad900a2e83ff60fbb753bbb`.
ITEM_SEQ는 50행 모두 비어 있지 않은 문자열이고 MTRAL_CODE·MTRAL_SN·TAMT_SEQ·QNT·
INGD_UNIT_CD는 각각 47행이 비어 있지 않은 문자열이다. CPNT_CTNT_CONT는 모두 null이다.
후보키 불완전 행은 3건, 완전 키 중 중복 그룹은 0건이다. 각 필드의 누락이 같은 행인지
이 집계만으로 단정하지 않는다. 상태는 SAMPLED_NOT_VERIFIED 그대로다.

위 초기 "미전환" 설명 이후, 선택 원본 키 기반 참조·제품별 순서 UNIQUE·release_profile
저장·무보정 migration 전환을 구현한다. 상세 계약과 기존 입력 호환성은
[결정안](../../governance/decisions/2026-09-11-catalog-component-occurrences.md)을 따른다.
#454가 병합돼 공용 계약 목차도 갱신한다. 이전 "공용 목차를 수정하지 않는다"는 작업 순서
제한은 해소됐다. 실제 MFDS 자동 수집→Component 적재가 완료됐다는 의미는 아니다.

## 전체 관찰 결과 보충

사용자 제공 126,825행 × 2회 전체 페이지 결과를 수령했다. 완전 키 95,128행은 중복 0건,
불완전 키는 31,697행이다. 반복 제품·원료 그룹 3,613개, 동일 이름·복수 원료코드 그룹
281개가 관찰됐다. 이전 실행 대기 기록 이후의 상태와 남은 의미 확인은
[전체 감사 결과](../../testing/mfds-component-key-audit-166.md)를 따른다. 실제 자동 적재 완료나 공식 매핑 승인으로 해석하지 않는다.
