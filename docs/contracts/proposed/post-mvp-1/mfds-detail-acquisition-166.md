# #166 D-04 MFDS 상세 수집·Snapshot 생산 계약

- 상태: Proposed / 수집기 구현 및 합성 검증을 포함한 PR 리뷰 대상. 실수집·운영 활성화 승인 아님.
- 구현: 김지혜. 담당 리뷰: 송은영 — Source Receipt·실패 보존·transaction.
- MFDS 의미·Candidate 경계: [#477 정현우 검토](https://github.com/AI-HealthCare-05/AH_05_04/pull/477#pullrequestreview-5190458759)의 관찰 키·출처·빈 행 원칙을 유지한다.
- [결정 기록](../../../governance/decisions/2026-09-13-mfds-detail-acquisition.md)

## 실행 범위와 진입점

`acquire_mfds_detail()`은 `LIST_PRODUCT_COMPONENT_DETAILS` Operation 잠금을 얻고
`getDrugPrdtMcpnDtlInq07` 전체 페이지를 수집한다. source/endpoint는 MFDS 제품 허가 출처를
사용하지만 제품 목록 Operation·Endpoint Receipt와는 구분한다.

이번 구현은 `FULL_ENDPOINT`만 지원한다. 요청은 type=json, pageNo, numOfRows=100 및 비밀
serviceKey로 고정하며 품목/업체 필터, 첫 N페이지, 호출자 선택 행 입력을 받지 않는다.
최대 1,500페이지·총 3,600초는 내부 중단 한도이지 제공기관 quota/페이지 상한 보장이 아니다.
한도에 도달하면 실패하며 부분 수집을 성공으로 승격하지 않는다.

기존 자동 실행·P0 Operation 목록에는 추가하지 않는다. DB Operation 등록, 실제 Endpoint Receipt
실측·승인, 실제 API 호출은 별도 실행 단계다. 운영 key나 성공 live Receipt를 이 PR에서 생성하지 않는다.

## 원문과 실패 보존

- 성공 코드로 해석된 HTTP 응답 bytes는 private 수집 디렉터리(0700)의 파일(0600)에 배타적으로 저장한다.
  같은 파일을 덮어쓰지 않는다. 인증정보/요청 URL은 파일명·보고서에 기록하지 않는다.
- 페이지 기본키 검사 전에 보존하므로 빈 행·반복 행도 원문에 남는다.
  해석 불가능한 응답이나 인증 실패 본문은 보관 대상으로 승격하지 않고 기존 안전한 실패 코드를 사용한다.
- `inspection.json`에는 실행 시각·범위·페이지 수·전체/관찰/중복/제외 건수, 제외 사유와 원문 페이지/행 위치를 남긴다.
  이 파일은 private 조사 bundle의 sidecar이며 DB 감사 테이블이나 Receipt가 아니다.
  원문 artifact는 기존 불변 저장소에 저장하고 실패 Run에 연결한다. sidecar의 보존·정리는 호출자가 담당한다.
- 전체 페이지와 건수가 맞으면 빈 행/중복까지 포함한 `observations.json`도 남긴다.
  실패한 수집의 canonical 파일이 존재해도 Snapshot 사용 가능 증거가 아니다.
- 빈 주요 성분 필드는 EMPTY_COMPONENT_FIELDS, 불완전/잘못된 행은 INVALID_COMPONENT_FIELDS,
  같은 관찰 키의 다른 원문은 CONFLICTING_OBSERVATION으로 집계한다.
  같은 키·동일 원문도 원본 배열에서 지우지 않는다. 기본키 중복이므로 Snapshot 생산은 차단한다.
- 빈 성분은 성분 없음으로 해석하지 않고 Ingredient/Component를 만들지 않는다.
  실패 시 Snapshot/CURRENT는 변경하지 않는다. DB/저장소 예외는 호출자의 transaction rollback으로 전파한다.

## 키·bytes·Receipt

관찰 키는 문자열 ITEM_SEQ + TAMT_SEQ + MTRAL_SN이며 기존 mfds-component-key-v1 표현을 유지한다.
숫자 변환·trim·이름 기반 코드 병합·가짜 빈 키를 사용하지 않는다. 이 키는 전역 안정 Identity가 아니다.
MTRAL_CODE는 기존 합의대로 MFDS 출처 내부 성분 코드로 다룬다.

상세 canonical bytes는 원문에서 다시 해석한 전체 배열이다. 페이지 순서와 각 페이지 내부 순서를 유지하고,
`ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False`의 UTF-8 JSON에 SHA-256을 적용한다.
버전은 기존 Loader의 `mfds-component-observations-v1`이다. 이 배열 순서는 공식 component_order가 아니다.
제품 checksum이나 D-05 projection hash로 재사용하지 않는다.

`build_detail_ingestion_result()`는 다음을 모두 확인한다.

1. 상세 Operation의 정확한 Endpoint Receipt 1.1 계약·hash·필수 합성 fixture hash.
2. 전체 수집 성공, 기본키 누락/중복 0, 연속 페이지, 일관된 totalCount 및 전체 관찰 건수.
3. 수집 페이지와 raw artifact의 1:1 연결·checksum·내용·페이지 번호·totalCount.
4. 전체 행의 Component 적격성 및 중복/충돌 없음.

**일부 행에 전체 Snapshot Receipt를 붙이는 방식은 금지한다.** 일부 페이지를 넘기거나 전체 수집 결과와
다른 원문을 넣으면 거부한다. 선택 결과의 checksum만 바꾸거나 거부 건수를 0으로 보정하지 않는다.
향후 필터/파생 subset은 독립 범위·자체 증빙·원본 연결을 갖춘 별도 계약이 필요하다.
페이지 건수 일치는 제공기관의 동일시점 Snapshot이나 장기 키 안정성을 보장하지 않는다.

Endpoint 검증기는 기존 제품 함수의 검사를 공통화해 상세 계약에도 적용한다. 제품용 공개 import/함수는
유지하며 제품 Receipt의 허용 조건은 완화하지 않는다. 합성 테스트용 Receipt는 테스트 임시 디렉터리에서만 생성한다.

## 저장과 재조회

`ingest_and_persist_mfds_detail()`은 검증된 전체 배열로 기존 Source lifecycle을 호출한다.
`SourceIngestionResult`는 기존 제품 저장 DTO와 같은 필드이며 ProductIngestionResult 이름은 호환 alias로 유지한다.
`persist_source_ingestion_result()`는 기존 lifecycle 구현을 공유하고 제품 함수 이름도 유지한다.
새 테이블·컬럼·migration·RLS·Trigger·DB 함수는 추가하지 않는다.

- parser_version: mfds-component-acquisition@1
- normalization_version/schema_version/source_version: 호출자가 명시하고 기존 version 검증에 따라 저장.
- rejected_record_count는 0만 허용한다. 제품 reject 계약 version을 상세에 붙이지 않는다.
- 빈 행·중복·검사 실패는 원문과 FAILED Run만 저장한다. 제한 적재로 우회하지 않는다.
- 정상 입력은 기존 PENDING Snapshot·integrity 검증·Run·원문 연결을 같은 DB transaction에서 기록한다.
- 기존 동일 내용 재사용·version 충돌·과거 Snapshot 보존 규칙을 그대로 적용한다.
- 호출자가 Operation 잠금부터 저장 완료까지 같은 transaction을 유지하고 commit/rollback한다.
  함수 반환과 commit 완료를 구분한다. 저장소의 content-addressed 원문은 rollback 시 즉시 삭제하지 않고 기존 orphan 정리 대상으로 둔다.

DB에서 재조회한 Receipt와 전체 canonical bytes를 기존 #477 Loader에 전달한다.
제품 Snapshot은 별도로 검증하고 명시적인 원료 매핑·order·승인 포트를 제공해야 한다.
이번 합성 테스트의 승인 대역·Product fixture는 실제 Source/Catalog 승인 저장소 연결이나 실제 제품 실수집 증거가 아니다.

## 실제 실행 전 남은 조건

PR 리뷰로 새 Operation·Receipt·저장 경계를 확인하고 병합한 후 실제 endpoint 소량 검증과 전체 수집을 진행한다.
실데이터에 빈 행이 있으면 원문·실패 기록만 보존한다. 실제 Snapshot 생산과 Catalog 인계 성공을 미리 약속하지 않는다.
공식 순서/의미 확인, 사용 승인, 실제 상세 Endpoint Receipt 확보는 별도이며 D-02·D-03·D-05 확정을 대신하지 않는다.
