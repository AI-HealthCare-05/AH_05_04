# #591 2그룹 일반약 5개 실제 적재·검증 결과

기록일: 2026-09-17. 구현·실행 담당 김지혜, 담당 리뷰어 송은영. 정책·서버 근거는 권가빈, consumer acceptance 담당은 정현우다.

## 결론과 근거 범위

2그룹 일반약 5개 제품의 EE·UD·NB·NN 총 20개 문서는 실제 Artifact 및 Snapshot·Member·Run 저장을 완료했다. 각 제품에서 CREATED → commit 후 재조회 → 동일 입력 NO_CHANGE·동일 Snapshot 확인 → 읽기 전용 인계 메타데이터 검증을 통과했으며 실행 종료 코드는 0이다.

근거는 실행 담당자가 전달한 서버 출력과 로컬 보존 수집 증빙·Receipt의 대조다. 문서 작성 과정에서 서버 작업을 새로 실행하지 않았다. 기계 판독 요약은 [group2-ingestion-results.json](group2-ingestion-results.json)에 있다. 상세 Member·Artifact·Run 대응표는 비공개로 보관하며 3그룹 완료 후 담당자 제한 채널에 함께 전달할 예정이다. 원문 XML, credential, 서버 경로 및 상세 대응표는 이 기록에 포함하지 않는다.

## 제품별 결과

| 제품 | ITEM_SEQ | Snapshot | 최초 / 재실행 | Member | NN |
| --- | --- | --- | --- | ---: | --- |
| 이지엔6애니연질캡슐(이부프로펜) | 200400463 | `f605c5ef-de51-4044-9c54-154205b5d560` | CREATED / NO_CHANGE | 4 | COMPLETE |
| 타이레놀정500밀리그람(아세트아미노펜) | 202106092 | `058faf73-a364-474e-a485-6383d8ec9076` | CREATED / NO_CHANGE | 4 | COMPLETE |
| 판콜에스내복액 | 198900672 | `7fe7072e-9d14-4707-97a9-3239ccc484de` | CREATED / NO_CHANGE | 4 | COMPLETE |
| 겔포스엠현탁액 | 199400883 | `1ccac7d3-128d-40fe-a9a5-9e0691e2279f` | CREATED / NO_CHANGE | 4 | PARTIAL_OFFICIAL |
| 지르텍정(세티리진염산염) | 200610765 | `2c9b5ac4-49f7-466b-a425-c1f76ef7a31e` | CREATED / NO_CHANGE | 4 | PARTIAL_OFFICIAL |

## 수행한 검증

- 실제 수집 기록의 시각·HTTP 관측값을 유지하고 XML 20개의 크기·raw SHA-256을 대조했다. #671 고정 commit `00cf01af86c467b20335e3cd90293747ea5e0ff8`의 발급 도구로 Receipt 2종과 acquisition manifest를 제품별 새 폴더에 생성했다. 이 SHA는 발급 구현 기준이며 최신 서버 checkout을 별도로 재조회했다는 의미는 아니다.
- 정책 ref `GABIN:591:EXPANSION16:EE_UD_NB:OTC5_NN`과 기술 ref `technical:591:eunyoung:xml-receipts-v1`를 기존 승인 원문에 연결했다. 식별자 자체를 전자서명으로 해석하지 않는다.
- 비어 있는 제품 입력 폴더에 XML 4개와 manifest를 반입하고 Receipt·approval/evidence는 별도 증빙 위치에 보관했다. 전달 파일 총 45개의 SHA-256·크기·0600 권한 및 서버 Receipt 검증을 통과했다.
- 기존 Source ACTIVE, Endpoint VERIFIED·APPROVED·ENABLED를 재사용하고 제품별 Operation을 APPROVED·ENABLED로 등록했다. commit 후 새 session 재조회로 등록값을 확인했으며 기존 Source/Endpoint 승인 메타데이터는 변경하지 않았다.
- profile 백업 후 확정값을 반영하고 Receipt와 결속 검증했다. READY 전환 전 다시 백업했으며 파일 재조회 일치를 확인했다. 읽기 전용 입력 mount에서 NN 포함 parser 사전검증을 통과했다.
- 첫 적재와 동일 입력 재실행에서 Snapshot ID, Member 4개, canonical checksum, source_version 일치를 확인했다. 인계 조회에서 20개 Member의 RAW_RESPONSE Artifact hash·크기·content type과 최초 성공 Run 결속, 각 제품 SUCCEEDED·NO_CHANGE Run을 대조했다.
- 제품 Receipt의 manifest_hash, Endpoint receipt_hash, Snapshot canonical_checksum은 서로 다른 hash 도메인으로 보존했다.

겔포스엠·지르텍 NN의 `PARTIAL_OFFICIAL`은 공식 원문의 빈 항목을 보존한 발급 도구 판정이다. 빈 항목에 합성 문구를 넣지 않았으며 Receipt와 서버 parser 검증은 통과했다. Source 적재 성공을 NN Document/Chunk materialization 지원·승인으로 해석하지 않는다.

## 남은 단계와 진행 경계

5개 Snapshot의 verification_status는 PENDING, verified_at/effective_at은 null이다. profile READY는 적재 준비 상태이며 Snapshot CURRENT와 다르다. Consumer acceptance는 5개 모두 PENDING이다.

1그룹 기록 당시에는 consumer acceptance 확인 후 2그룹 적재를 계획했으나, 이후 실행 담당자가 전달한 현우님의 안내에 따라 3그룹 Source 적재까지 진행한 뒤 consumer 확인을 묶어서 요청하는 방향으로 변경했다. 이는 인계 시점 조정이며 consumer acceptance 완료 선언이 아니다. 3그룹 실제 적재는 아직 미실행이고, 이번 2그룹 결과 PR을 먼저 기록·검토한 후 진행한다. 공통 consumer 결함이 확인되면 영향 범위를 확인하고 확대를 중단한다.

CURRENT 선택, Document/Chunk materialization, embedding·Knowledge Index·RAG·Runtime/Citation 및 서비스 공개는 수행하지 않았다. 가빈·은영님의 노바스크 특정 Snapshot 승인 범위를 이 제품들에 자동 적용하지 않는다. #591 전체 완료나 이슈 종료를 요청하지 않는다.

## 문서 검증 및 리뷰 요청

이번 변경은 기존 코드 실행 결과 기록이며 코드·schema·migration·권한 변경이 없다. JSON 파싱, 제품별 첫 적재·재실행·인계 기록과 Receipt 대조, 문서/JSON 일치 및 diff 공백 검사를 수행했다. 전체 애플리케이션 테스트와 서버 적재는 문서 PR 검증을 위해 재실행하지 않았다.

송은영: 생산자 완료 근거, hash 도메인 분리, DB 재조회·멱등성, NN 공식 공백 보존, consumer 대기 및 3그룹 진행 경계의 표현을 확인한다.
