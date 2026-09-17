# #591 3그룹 8개 제품 실제 적재·검증 결과

기록일: 2026-09-17. 구현·실행 담당 김지혜, 담당 리뷰어 송은영. 정책·서버 근거는 권가빈, consumer acceptance 담당은 정현우다.

## 결론과 근거 범위

3그룹 8개 제품의 EE·UD·NB 총 24개 문서는 실제 Artifact 및 Snapshot·Member·Run 저장을 완료했다. 각 제품 CREATED 후 commit 재조회, 동일 입력 NO_CHANGE·동일 Snapshot 확인, 읽기 전용 인계 메타데이터 검증을 통과했다. 실행 종료 코드는 모두 0이다.

근거는 실행 담당자가 제공한 서버 출력과 로컬 수집 증빙·Receipt 대조다. 문서 작성 중 서버 작업을 새로 실행하지 않았다. 기계 판독 요약은 [group3-ingestion-results.json](group3-ingestion-results.json)에 있다. 원문 XML, credential, 서버 경로, 상세 Member·Artifact·Run 식별자 대응표는 포함하지 않는다.

## 제품별 결과

| 제품 | ITEM_SEQ | Snapshot | 최초 / 재실행 | Member |
| --- | --- | --- | --- | ---: |
| 다이아벡스엑스알서방정500밀리그램(메트포르민염산염) | 200511904 | `23beb07d-4cc9-47fb-b899-c4b861bcf442` | CREATED / NO_CHANGE | 3 |
| 엑스포지정 5/80밀리그램(암로디핀베실산염, 발사르탄) | 200703804 | `c88a8480-0806-42fd-aca0-32710d6ff0da` | CREATED / NO_CHANGE | 3 |
| 코자정(로사르탄칼륨) | 200811814 | `f9bc0051-b21e-4bf2-9782-018809c2100c` | CREATED / NO_CHANGE | 3 |
| 자누비아정100밀리그램(시타글립틴인산염수화물) | 200710759 | `ec481ae0-22e1-4e85-ba04-35a789b316ab` | CREATED / NO_CHANGE | 3 |
| 자디앙정10밀리그램(엠파글리플로진) | 201403744 | `23b35a5d-bb76-4c30-a659-ed30484265c8` | CREATED / NO_CHANGE | 3 |
| 아마릴정2밀리그람(글리메피리드) | 199600864 | `891bd3f5-3adf-4afa-b692-57105a30ca26` | CREATED / NO_CHANGE | 3 |
| 크레스토정5밀리그램(로수바스타틴칼슘) | 200511256 | `8744b0da-7dd8-453f-b168-903395bd435f` | CREATED / NO_CHANGE | 3 |
| 이지트롤정(에제티미브) | 200410337 | `73020240-d891-4ef7-94ca-b443f052a898` | CREATED / NO_CHANGE | 3 |

## 수행한 검증

- 실제 수집 기록의 시각·HTTP 관측값을 유지하고 XML 24개의 byte size·raw SHA-256을 대조했다. #671 고정 commit `00cf01af86c467b20335e3cd90293747ea5e0ff8`으로 제품별 새 폴더에서 Receipt 2종과 acquisition manifest를 발급하고 별도 verify를 통과했다. 이 SHA는 발급 구현 기준이며 최신 서버 checkout의 독립 재조회 결과는 아니다.
- 정책 ref `GABIN:591:EXPANSION16:EE_UD_NB:OTC5_NN` 및 기술 ref `technical:591:eunyoung:xml-receipts-v1`를 기존 승인 원문에 연결했다. 식별자 자체는 전자서명이 아니다. 3그룹에는 NN을 포함하지 않았다.
- 서버의 빈 제품 입력 폴더에 XML 3개와 manifest를 반입하고 증빙은 별도 위치에 보관했다. 전달 파일 64개의 checksum·크기·0600 권한과 서버 Receipt verify를 확인했다.
- Source ACTIVE, Endpoint VERIFIED·APPROVED·ENABLED를 재사용하고 제품별 Operation만 APPROVED·ENABLED로 신규 등록했다. commit 후 새 session 재조회에 성공했으며 기존 Source/Endpoint 승인 메타데이터는 수정하지 않았다.
- profile 백업 후 확정값 반영·Receipt 결속 검증을 완료하고 다시 백업한 뒤 READY 전환·파일 재조회 일치를 확인했다. 읽기 전용 입력 mount에서 parser 결과의 원문 hash·canonical checksum·source_version을 발급본과 대조했다.
- CREATED 및 동일 입력 NO_CHANGE의 Snapshot ID·Member 3개·canonical checksum·source_version 일치를 확인했다. 인계 조회에서 24개 Member의 RAW_RESPONSE Artifact 크기·hash·content type 및 최초 성공 Run 결속을 대조했다. 제품별 SUCCEEDED·NO_CHANGE 2개 Run, 총 16개 Run이 관측됐다.
- 제품 Receipt manifest_hash, Endpoint receipt_hash, Snapshot canonical_checksum은 서로 다른 hash 도메인으로 유지했다. 마지막 인계 조회는 DB 메타데이터 조회이며 raw XML bytes를 다시 읽거나 consumer acceptance를 실행한 것이 아니다.

## 전체 진행 상태와 남은 단계

[1그룹](group1-ingestion-record.md) 3개 제품·9개 문서, [2그룹](group2-ingestion-record.md) 5개 제품·20개 문서, 이번 3그룹 8개 제품·24개 문서를 합쳐 확장 대상 16개 제품·53개 XML의 생산자 측 적재·재실행·인계 메타데이터 검증을 완료했다. 별도 선행 제품인 노바스크는 이 집계에 포함하지 않는다.

3그룹 8개 Snapshot의 verification_status는 PENDING이며 verified_at/effective_at은 null이다. profile READY는 Snapshot CURRENT를 의미하지 않는다. Consumer acceptance는 아직 PENDING이다.

현우님의 묶음 확인 안내에 따라 2·3그룹 13개 제품·44개 XML의 상세 인계 자료를 로컬에서 준비했다. 실행 담당자가 담당자 제한 채널로 전달할 예정이며, 실제 전달 완료나 consumer PASS를 이 문서로 선언하지 않는다. 1그룹은 기존 별도 인계 범위다. 2그룹 겔포스엠·지르텍 NN의 PARTIAL_OFFICIAL도 통합 인계 자료에 명시했다.

CURRENT 선택, Document/Chunk materialization, embedding·Knowledge Index·Retrieval·RAG·Runtime/Citation 및 서비스 공개는 이번 실행 범위가 아니다. 노바스크 특정 Snapshot의 승인을 다른 제품에 자동 적용하지 않는다. #591 전체 완료나 이슈 종료를 요청하지 않는다.

## 문서 검증 및 리뷰 요청

문서·JSON 기록만 추가하며 코드·schema·migration·권한 변경은 없다. JSON 파싱, 첫 적재·재실행·인계 출력과 Receipt 대조, 문서/JSON 일치, 비공개 필드 제외 및 diff 공백 검사를 수행했다. 전체 애플리케이션 테스트나 서버 적재를 문서 PR 검증을 위해 재실행하지 않았다.

송은영: 생산자 완료 근거, hash 도메인 분리, DB 재조회·멱등성, 전체 집계 및 consumer 대기·공개 경계 표현을 확인한다.
