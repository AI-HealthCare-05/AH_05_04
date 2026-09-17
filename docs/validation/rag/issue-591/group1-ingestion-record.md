# #591 1그룹 3개 제품 실제 적재·검증 결과

기록일: 2026-09-17. 구현·실행 담당 김지혜, 담당 리뷰어 송은영. 서버 설정·정책 근거는 권가빈, consumer acceptance 담당은 정현우다.

## 결론과 근거 범위

다이아벡스·리피토·다이크로짇 3개 제품은 실제 Artifact 및 Snapshot·Member·Run 저장을 완료했다. 각 제품 CREATED → commit 후 새 DB session 재조회 → 동일 입력 NO_CHANGE·동일 Snapshot 확인 → 읽기 전용 인계 메타데이터 조회를 통과했다. 각 실행 종료 코드는 0이다.

근거는 실행 담당자가 전달한 서버 출력과 로컬 보존 수집 증빙·Receipt의 대조다. 이 문서 작성 과정에서 서버 작업을 새로 실행한 것은 아니다. 상세 비공개 Member·Artifact·Run 대응표는 제한 채널로 인계했고 이 문서에는 포함하지 않는다.

기계 판독 요약은 [group1-ingestion-results.json](group1-ingestion-results.json)에 있다. 최종 발급·실행 기준은 #671 병합 commit `00cf01af86c467b20335e3cd90293747ea5e0ff8`이다.

## 제품별 결과

| 제품 | ITEM_SEQ | Snapshot | 최초 / 재실행 | Member |
| --- | --- | --- | --- | ---: |
| 다이아벡스정500밀리그램(메트포르민염산염) | 198500321 | `aa703936-6c80-4267-a608-62b81e0367a9` | CREATED / NO_CHANGE | 3 |
| 리피토정10밀리그램(아토르바스타틴칼슘삼수화물) | 200410090 | `a5811293-5437-4f00-be44-9e65a0b32914` | CREATED / NO_CHANGE | 3 |
| 다이크로짇정(히드로클로로티아지드) | 196000008 | `d4834276-f2b7-48d9-bbe9-c9a29541097b` | CREATED / NO_CHANGE | 3 |

## 수행한 검증

- 실제 수집 XML의 byte size·raw SHA-256을 보존 evidence와 대조했다. 수집 시각·관측 content type을 추정하거나 현재 시각으로 바꾸지 않았다.
- 가빈님 공식 정책 ref `GABIN:591:EXPANSION16:EE_UD_NB:OTC5_NN`을 반영해 새 폴더에서 Receipt 2종과 acquisition manifest를 발급했다. 은영님 A~D·4~7절 승인 원문은 기존 opaque technical ref `technical:591:eunyoung:xml-receipts-v1`에 연결해 보관했다. 이 ID 자체가 전자서명은 아니다.
- 업로드 파일 SHA-256을 로컬 원본과 대조하고 서버 Receipt verify와 profile 결속 verify를 통과했다. 다이아벡스의 기존 manifest는 입력 폴더 밖 제한 백업 후 교체했고, 리피토·다이크로짇은 빈 제품 입력 폴더에 새로 반입했다.
- 기존 Source ACTIVE, Endpoint VERIFIED·APPROVED·ENABLED를 재사용했다. 제품별 Operation만 APPROVED·ENABLED로 신규 등록하고 새 session에서 재조회했다. 기존 Source/Endpoint 승인 메타데이터는 변경하지 않았다.
- 등록·재조회와 Receipt/profile 결속 검증 후 profile READY로 전환했다. 읽기 전용 입력 mount에서 parser 사전검증을 통과했다.
- 첫 적재 CREATED 및 새 session 검증, 동일 입력 NO_CHANGE·동일 Snapshot·Member 3개·canonical checksum·source_version 일치를 확인했다.
- 인계 조회에서 Snapshot·Member·Artifact·Run 대응과 저장 content_type을 대조했다. 이 마지막 조회 자체는 raw XML bytes를 다시 읽지 않았으며 consumer acceptance를 대신하지 않는다.

기존 코드 실행 결과를 기록하는 문서 변경이다. 코드·schema·migration·권한 정책 변경은 없다. 이번 PR을 위해 전체 애플리케이션 테스트를 재실행하지 않았으며, JSON 파싱·제품별 결속 및 기록 대조·diff 공백 검사를 수행했다.

## 남은 단계와 공개 경계

세 Snapshot은 모두 verification_status=PENDING이며 verified_at/effective_at은 null로 관측됐다. profile READY는 적재 준비 상태이고 Snapshot CURRENT를 의미하지 않는다.

consumer acceptance는 세 제품 모두 결과 대기다. 담당자는 인계 자료를 전달했다고 확인했으며, 현우님 요청에 따라 리피토·다이크로짇 자료는 묶어서 전달했다. 이 PR의 승인을 consumer acceptance 완료로 간주하지 않는다.

2그룹 일반약 5개의 실제 DB 적재는 아직 시작하지 않았다. 이번 계획은 PR 검토와 1그룹 consumer acceptance 완료 확인 후 2그룹 실제 적재를 진행하는 것이다. 공통 consumer 결함이 발견되면 확대를 중단한다.

CURRENT 선정, 내부 RAG, Document/Chunk materialization, embedding/Index, Runtime/Citation 및 서비스 공개 승인은 이번 기록에 포함하지 않는다. 노바스크의 별도 승인·실행 범위를 이 세 제품에 자동 적용하지 않는다. #591 전체 완료 또는 이슈 종료를 요청하지 않는다.

## 리뷰 요청

송은영: 생산자 완료 증빙과 승인·hash 결속, DB 재조회·멱등성 결과, consumer 대기 및 2그룹 진행 경계의 표현을 확인한다. 제한 채널 원본 확인이 필요하면 해당 인계 자료로 대조한다.
