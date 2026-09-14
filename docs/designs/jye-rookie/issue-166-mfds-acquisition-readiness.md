# #166 D-04 MFDS 상세 수집·Snapshot 생산 착수 조사

- 상태: 조사 기록에 수집기 구현을 연결한 PR 준비. 실제 API 호출·운영 Snapshot 생산 미실행.
- 최초 조사 기준: develop `cacdd3e2`; 구현 기준: #483 병합 후 develop `1b34d49d`. 공식 페이지 조회: 2026-09-13.
- AGENTS.md·CONTRIBUTING.md 확인. Python 검증·transaction으로 무결성을 관리하고 RLS·Trigger·업무용 DB 함수는 추가하지 않는다.
- #483과 분리한 `feat/166-mfds-detail-acquisition` 구현 브랜치. 아래 기존 경로 표는 최초 조사 시점이며 최신 구현은 연결 계약을 따른다.
- [상세 수집 구현 계약](../../contracts/proposed/post-mvp-1/mfds-detail-acquisition-166.md): 전체 범위만 구현, 빈 행 포함 입력은 실패 보존.

## 공식 명세 재확인

[MFDS 의약품 제품 허가정보 공식 페이지](https://www.data.go.kr/data/15095677/openapi.do)의 HTML 내
Swagger 2.0 `paths./getDrugPrdtMcpnDtlInq07`를 직접 확인했다. 인증키나 개인 계정은 사용하지 않았다.

- HTTPS host: apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService07.
- 요청: serviceKey 필수. pageNo·numOfRows·type 및 업체/제품명 필터가 있다.
- type 기본값은 XML이며 JSON을 명시할 수 있다.
- 해당 path의 요청 parameters에는 ITEM_SEQ 필터나 정렬 지정 필드가 없다.
  품목코드를 query에 넣으면 서버가 필터링한다고 가정하지 않는다.
- 응답은 ITEM_SEQ(품목기준코드), MTRAL_CODE(원료코드), MTRAL_SN(일련번호),
  TAMT_SEQ(총량일련번호), QNT(분량), INGD_UNIT_CD(분량단위정보)를 문자열로 기술한다.
- 이 명세에는 후보 복합키의 전역 유일성·재수집 안정성·일련번호 그룹 범위·정렬 보장이 없다.
- schema의 문자열 선언만으로 실제 null/빈 행을 거부·삭제해도 된다는 계약이 생기지 않는다.
- 페이지 크기 상한·동일시점 전체 Snapshot·필터 정확 일치도 해당 명세로 확정되지 않는다.

공식 필드명 확인과 의미·순서 승인을 구분한다. MTRAL_CODE를 전역 정규화 성분 Identity로
승격하지 않고 MFDS 출처 내부 코드로 보존하는 기존 D-04 경계를 유지한다.

## 기존 수집 경로

| 단계 | 코드 근거 | 확인 결과 |
| --- | --- | --- |
| Endpoint 후보 | ai_worker/tasks/rag/source_client/endpoints.py | 제품 목록 LIST_APPROVED_PRODUCTS는 등록돼 있지만 주성분 상세 Operation은 없음 |
| 다중 페이지·동시 실행 | source_client 및 source_ingestion/service.py | 재사용 가능한 수집·Operation 잠금 구조가 있음. 새 endpoint의 승인 Receipt/계약을 대신하지 않음 |
| Parser/checksum | source_ingestion/parse.py·checksums.py | 제품 목록 Operation과 ITEM_SEQ 단일 PK를 명시적으로 요구함 |
| 검증 결과·저장 인계 | source_ingestion/result.py·receipt_validation.py·persistence.py | ProductIngestionResult·제품 전용 endpoint 증빙과 reject 계약에 연결됨 |
| 상세 조사 스크립트 | scripts/rag/probe_mfds_component_fields.py·audit_mfds_component_keys.py | 관찰·집계 도구. production Snapshot/Receipt 생성 경로가 아님 |
| 상세 Loader | catalog/mfds_loader.py | 이미 존재하는 상세 canonical bytes·Snapshot Receipt와 Product 입력, 원료 매핑·명시적 order를 받아 Catalog로 연결 |

현재 Loader는 detail Receipt의 canonicalization_spec이 `mfds-component-observations-v1`인지,
전체 JSON 배열 bytes/checksum과 출처가 맞는지 검증한다. 제외/거부가 있는 자료로 부분 build를 만들지 않으며
제품·원료·순서 입력의 범위도 전체 관찰과 일치해야 한다. 실제 HTTP 수집과 Source 승인 자체는 수행하지 않는다.

## 수집기 연결 전에 정할 핵심 경계

1. **상세 Operation과 endpoint 증빙:** 별도 operation_code, 응답 모양·성공/오류·페이지 제한·필수 필드·권한 검증을 정의한다.
   제품 Receipt를 복사하거나 endpoint URL만 교체해 사용하지 않는다.
2. **원본 행과 Component 적격성:** 기존 사용자 전체 관찰에서 126,825행 중 31,697행은 주요 성분 필드가 비었다.
   전체 원본 수집과 적격 Component 선별을 구분해야 한다. 빈 키를 임의 생성하거나 행을 조용히 삭제하지 않는다.
3. **키와 canonicalization:** ITEM_SEQ 단일 PK나 제품 checksum을 재사용하면 반복 성분이 충돌/손실될 수 있다.
   완전한 상세 행의 관찰 복합키, 빈 행 보존, 중복/상충 및 배열 정렬 규칙을 함께 정한다.
   Source bytes와 Loader가 검증하는 상세 canonical bytes의 범위가 같아야 한다.
4. **전체 수집과 제한 적재:** 현재 Loader는 제외 없는 정확한 범위를 요구한다. 따라서 전체 API 응답에서
   일부만 뽑은 bytes에 전체 Snapshot receipt를 붙이면 안 된다. 제한 범위를 허용하려면 포함/제외 기준,
   원본 및 제외 보고서 보존·완전성 상태·출처 결속을 별도 계약으로 정해야 한다.
5. **순서:** 숫자형 TAMT_SEQ/MTRAL_SN 또는 HTTP 배열 순서를 공식 component_order로 치환하지 않는다.
   공식 근거를 확보하거나, 관찰값을 보존하는 내부 순서 규칙을 명시적으로 검토받는다.

## 상세 수집 계약 구체안 — 검토 전 제안

아래는 기존 Loader의 검증을 완화하지 않고 수집기를 연결하기 위한 제안이다.
새 Operation·Receipt·canonicalization의 확정 계약이나 구현 완료를 뜻하지 않는다.

### 1. 빈 성분 행 보존

- HTTP 응답 원문은 페이지 단위 artifact로 보존한다. 요청 범위, 페이지 번호, 페이지 내 위치,
  응답 건수, 수집 시각, artifact checksum을 연결한다. 인증키는 요청 기록에서 제거한다.
- 파싱한 전체 상세 배열에도 빈 성분 행과 동일 내용의 반복 행을 그대로 보존한다.
  null·빈 문자열·필드 부재 및 문자열 원문을 임의 보정하지 않는다.
- artifact/페이지/행 위치는 원문을 찾는 참조일 뿐, 성분 Identity나 안정적인 상세 행 키가 아니다.
  빈 TAMT_SEQ/MTRAL_SN을 위치값·0·가짜 코드로 채우지 않는다.
- 기존 검사기의 EMPTY_COMPONENT_FIELDS·INVALID_COMPONENT_FIELDS·CONFLICTING_OBSERVATION
  구분에 따라 원문 참조·사유·건수·대상 품목·입력 범위를 기록한다. 빈 행은 성분 없음의 증거가 아니며
  Ingredient/Component를 생성하지 않는다. 성분명만 있고 코드가 없는 행도 추론 매핑하지 않는다.
- 검사기가 동일 키·동일 원문의 관찰을 하나로 모으는 동작과 Source 원문 보존을 구분한다.
  원문 및 checksum 계산 배열에서는 중복을 지우지 않고 duplicate_count를 별도 기록한다.
- input_count = observations 건수 + duplicate_count + exclusions 건수로 대조한다.
  수집·원문 보존 완료와 Catalog 적격성을 별도 기록한다. 제외가 있으면 현행 Loader에 따라
  Catalog 적재를 중단하고, 누락 없는 Catalog 또는 READY 상태라고 보고하지 않는다.

### 2. 상세 행 키·checksum

| 대상 | 제안 및 현재 경계 |
| --- | --- |
| 완전한 상세 관찰 행 키 | 기존 mfds-component-key-v1의 ITEM_SEQ + TAMT_SEQ + MTRAL_SN 문자열 조합 유지. Snapshot 안의 관찰 참조이며 전역/장기 안정 키가 아님 |
| 문자열 보존 | 앞자리 0 제거·숫자 변환·trim으로 키를 바꾸지 않음. 원료명으로 코드 병합하지 않음 |
| 같은 키의 다른 원문 | 충돌로 기록하고 Catalog 인계 중단. 마지막 행 덮어쓰기 금지 |
| 같은 키의 같은 원문 | Source 배열에 출현 횟수까지 보존. 검사기의 중복 집계와 구분 |
| 키 불완전 행 | 원문 위치로 추적하되 관찰 키를 발급하지 않음. 기존 PK 검증을 통과한 것으로 처리하지 않음 |
| 원문 artifact checksum | 실제 응답 bytes의 무결성 확인. 파싱한 배열의 canonical checksum과 별개 |
| 상세 canonical checksum | 빈 행·중복을 포함한 해당 수집 범위의 전체 JSON 배열 bytes에 SHA-256 적용. 제품 ITEM_SEQ 단일 키 checksum 재사용 금지 |
| Receipt 결속 | operation·Snapshot ID/version·범위·건수·canonicalization 버전·checksum·원문 manifest가 같은 입력을 가리켜야 함 |

현행 Loader가 요구하는 mfds-component-observations-v1은 JSON 배열을 UTF-8,
ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False로 직렬화한
bytes를 검증한다. 객체 키 정렬과 배열 순서는 구분한다. 수집기 연결 제안은 페이지 번호 순서와
페이지 안의 응답 순서를 유지해 배열을 구성하고, 동일 원문 재현 시 같은 bytes를 얻도록 하는 것이다.
이를 공식 성분 표시 순서로 사용하지 않는다. 재수집 시 행 순서가 달라지면 checksum도 달라질 수 있다.
순서 독립 정렬이나 중복 제거를 추가하려면 별도 계약 변경으로 검토하며 기존 spec 이름 아래 몰래 적용하지 않는다.

현재 Source Receipt 검증은 PK 누락·중복 0을 요구한다. 따라서 빈 키 행을 포함하는 전체 응답을
기존 제품 검증 결과에 끼워 넣어 성공 Receipt로 만들 수 없다. 상세 전용 검증 결과에서
원문 완전성·관찰 키 적격성·Catalog 사용 가능성을 어떻게 구분할지 검토하고 구현해야 한다.
그 전에는 원문 보존/조사 결과만 남기며 검증 완료 Snapshot Receipt 발급을 주장하지 않는다.

### 3. 제한 적재 범위

**전체 Snapshot에서 일부 행을 선택한 뒤 전체 Snapshot Receipt를 붙이는 방식은 금지한다.**
checksum만 다시 계산하거나 rejected_record_count를 0으로 바꾸는 것도 허용하지 않는다.

| 상황 | 처리 기준 |
| --- | --- |
| 전체 endpoint 수집 | 선언한 요청 범위의 모든 페이지·행을 보존하고 응답 건수와 대조. 페이지 순회가 동일 시점 전체 자료를 보장한다고 주장하지 않음 |
| 공식 필터를 사용한 제한 수집 | 실제 지원되는 필터와 값을 범위로 기록하고 그 범위의 모든 페이지를 수집. 전국/전체 품목 Snapshot으로 표시하지 않음 |
| 첫 N페이지·N행만 조사 | 조사 artifact로 보존. 완전한 수집이나 Catalog용 검증 Receipt로 승격하지 않음 |
| 전체 수집 후 빈 행·원하는 품목만 제거 | 현행 인계에서는 불허. 원본 Receipt를 선택 결과에 재사용하지 않음 |
| 향후 파생 subset 필요 | 별도 변환 계약·선택 기준 버전·원본 Snapshot 연결·포함/제외 manifest·자체 checksum·자체 Receipt를 갖춘 독립 자료로 설계/검토. 이번 제안만으로 허용하지 않음 |

제한 범위의 기록에는 Operation, 인증정보를 제외한 요청 필터, 페이지 크기/실제 페이지 수,
응답 totalCount와 관찰 건수, 시작/종료 시각, 포함·제외 기준 및 사유별 건수,
원문 manifest, 범위 내 수집 완전성과 Catalog 적격성 판정을 포함한다.
페이지 실패·건수 불일치·검증 실패가 있으면 원문과 실패 기록을 보존하고 성공 인계를 중단한다.
건수 일치만으로 페이지 사이 누락/중복 또는 수집 중 데이터 변경이 없었다고 보증하지 않는다.

Product 입력도 상세 관찰의 품목 범위와 정확히 맞아야 한다. Product Receipt에서 일부 Product만
선택하는 경우 역시 기존 승인된 선택/출처 계약이 있는지 확인해야 하며, 전체 Receipt만 첨부했다고
정합성이 확보됐다고 판단하지 않는다. 현재 Loader의 집합 일치 검사는 이 증빙 검토를 대신하지 않는다.

## 조사 문서 완료 이후의 착수·완료 조건

문서 기록 완료는 실제 수집·Snapshot 생산 완료나 즉시 운영 실행 허가가 아니다.
다만 다음 구현을 준비하기 위해 D-02·D-03·D-05의 확정을 기다릴 필요는 없다.

1. **계약 검토:** 상세 Operation, 빈 키 행의 Source 검증 상태, checksum 배열 순서,
   제한 범위와 Receipt 결속을 구체안으로 검토한다. 현우님은 MFDS 의미·Candidate 인계,
   은영님은 공유 Source/Receipt·저장 무결성 변경 범위를 확인한다.
   DB 변경 필요 여부는 기존 저장 구조를 대조한 후 판단하며 미리 migration을 전제하지 않는다.
2. **구현·합성 검증:** 기존 수집 client를 이용한 상세 전용 parser/checksum/검증 결과·저장 연결을
   구현한다. 빈 행 보존, 중복/충돌, 페이지 실패, 건수 불일치, 범위 불일치,
   subset + 전체 Receipt 거부, 저장 rollback, Receipt 재조회를 검증한다.
   이 준비에는 실제 API 키가 필요하지 않다.
3. **실제 수집 검증:** endpoint 승인·검증 기준과 실행 설정을 갖춘 뒤 키로 소량 확인하고,
   선언한 범위 전체를 수집하여 원문·manifest·checksum·Snapshot/Receipt를 재조회 대조한다.
   검증 불충족이면 원문/실패 기록만 남기고 성공 Snapshot 생산으로 보고하지 않는다.
4. **Catalog 인계:** 제외 없는 정확한 범위, 명시적 원료 매핑·order 계약 및 필요한 사용 승인을
   충족할 때 #477 Loader → DB → Candidate를 검증한다. 빈 행이 있는 전체 수집의 보존 성공을
   Catalog 적재 성공으로 바꾸지 않는다. 실제 수집은 공식 순서·의미 확인을 대신하지 않는다.

비즈니스 판정·범위 확인·원자 저장은 Python Service/Repository의 명시적 검증과 transaction으로
구현한다. RLS·DB Trigger·업무용 DB 함수는 추가하지 않는다.

## 다음 구현 단위 제안

- 먼저 상세 Source 수집 계약의 입력·키·빈 행·canonical bytes·부분 범위를 문서화한다.
- Source client의 기존 페이지 처리·원본 artifact 저장·실패 기록·Operation 잠금 재사용 지점을 정한다.
- 제품 경로를 그대로 유지하면서 상세 전용 parser/checksum/검증 결과/Receipt 생성 경계를 연결한다.
- 독립 상세 Snapshot 생성 → Receipt 재조회 → #477 Loader까지 합성 통합 검증한다.
- 실제 API 키는 endpoint 소량 실측과 최종 수집 검증 시 필요하다. 코드·문서에 키를 저장하지 않는다.
- Snapshot 생산 성공과 Source/Catalog 사용 승인·Runtime 활성화는 별개다.

공식 순서 보장과 빈 상세 행 의미는 현재 명세에서 확인되지 않았다. 이미 수행한 전체 조회를 반복해도
이 의미를 자동으로 입증하지 못하므로, 제공기관 자료/문의 또는 명시적인 내부 처리 계약이 필요하다.
이번 조사로 새 공식 의미·사용 승인·공유 계약이 확정됐다고 표시하지 않는다.

## 관련 기록

- [기존 MFDS 키 관찰](../../testing/mfds-component-key-audit-166.md)
- [D-04 Loader 인계](issue-166-d04-loader-handoff.md)
- [D-04 관찰 출처 결정](../../governance/decisions/2026-09-13-component-observation-handoff.md)

검증: 공식 HTML 내 Swagger 응답·path parameters 확인, 위 코드 경로 대조, 문서 링크·Markdown 검토.
위 최초 조사는 실제 MFDS 호출·DB 쓰기 없이 수행했다. 이후 구현의 합성 DB 검증은 [별도 검증 기록](../../testing/mfds-detail-acquisition-166.md)에 구분한다.
