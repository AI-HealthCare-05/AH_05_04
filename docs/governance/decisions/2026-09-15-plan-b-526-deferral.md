# Plan B 제품별 근거 RAG·#526 추가 구현 보류 근거

- 기록일: 2026-09-15
- 상태: 사전 확인 결과와 기존 중단 합의에 따른 작업 보류 기록. 문서 리뷰 대상이며 새로운 계약 승인이나 운영 활성화를 뜻하지 않는다.
- 적용 범위: 이번 Plan B 제품별 근거 RAG를 위한 #526 잔여 구현 착수. #526 전체 폐기·완료 또는 다른 용도의 구현 중단을 의미하지 않는다.
- 확인 기준: 사전 확인 당시 develop `eaa1f396`. 문서 작성 시 `3e0c4f4b`에서도 환자용 복약정보 Receipt가 동일함을 재확인했다. 실측을 새로 수행한 결과가 아니라 기존 Receipt와 구현 기록을 검토한 결과다.

## 1. 중단 기준의 근거

김지혜가 공유한 9월 15일 10:36 정현우 답변은 기존 승인 Source/Snapshot만으로 제품 Identity와 MFDS 환자용 복약정보를 연결할 수 있는지 짧게 확인하되, 부분 Catalog 구성 또는 자연키·locator·추가 승인 문제 해결이 필요하면 이번 범위에서 제외하도록 제안했다. 이는 조건부 범위 합의이며, 사전 확인 결과를 현우님이 별도로 재승인했다는 의미는 아니다. 원문은 사전 확인에 사용한 대화 캡처로 확인했으며 저장소에서 추적 가능한 메시지 링크는 아직 확보하지 않았다.

## 2. 확인된 사실과 해석

| 확인된 사실 | 보류 판단에 미치는 영향 |
| --- | --- |
| 환자용 복약정보 기존 Receipt: 4,782행, `itemSeq` 중복 17건, 완전 중복 행 0건 | 단순 중복 제거로 해결됐다고 간주할 수 없다. |
| `BLOCKED_BY_UNSTABLE_PRIMARY_KEY`, `parser_activation_allowed=false` | 기존 수집 경로는 차단 상태이며, 합의한 자연키 관련 중단 조건에 해당한다. |
| 제품 목록 Endpoint 검증 통과 | 제품별 승인 Catalog·환자용 복약정보·Citation 연결 완료를 입증하지 않는다. |
| 사전 확인 당시 develop에서 해당 차단 해소 변경을 확인하지 못함 | 즉시 인계 가능한 승인 연결 세트의 증빙을 확보하지 못했다. |
| 기존 Source→Catalog 검증은 합성 Snapshot·합성 승인 verifier 기반 | 실제 승인 데이터의 연결·인계 증빙으로 대체할 수 없다. |

승인 저장소를 연결하는 #526 구현만으로 환자용 복약정보 자연키 문제는 해결되지 않는다. 따라서 이번 제품별 근거 RAG를 목적으로 추가 구현을 먼저 진행하지 않는다.

## 3. 이번 우선 범위와 제외 범위

- 우선: 확정 처방 정보 안내, Safety Intake, 긴급·응급 분기, 범위 제한, 근거 부족 시 안전 종료.
- 보류: 적격 근거 데이터 연결을 전제로 하는 제품별 근거 RAG와 이를 위한 #526 추가 구현 착수.
- 사전 확인에서 제외: QNT 차단 데이터로 부분 Catalog 구성, 자연키·locator 신규 정의·구현, 추가 Source 승인, 기존 계약 변경.
- 이 판단은 위 우선 기능의 구현·배포·공개 검증이 완료됐다는 선언이 아니다. Track C #193의 미복용 Safety·Barrier 흐름과도 구분한다.

## 4. 재개 조건

1. 기존 계약 안에서 제품 Identity·필수 데이터가 완전한 승인 Source/Snapshot과 동일 제품의 MFDS 환자용 복약정보 연결 증빙이 제시된다.
2. 그 근거를 특정하는 자연키·locator·승인 자료가 이미 갖춰져 있음을 확인한다. 차단된 경로를 사용할 경우 차단 해소 증빙도 필요하다.
3. 현우님과 기존 구현으로 연결 가능한지 확인하고 이번 시연 범위·일정에 재포함할지 정한다.

새 계약이나 승인이 필요한 해결 작업은 별도 후속으로 추적한다. 운영 DB·팀 비공개 승인 저장소는 조회하지 않았으므로, 적격 제품이 전혀 없다고 결론 내리지 않는다. 별도로 이미 승인된 연결 자료가 있다면 그 자료에 한해 재확인할 수 있다.

## 5. 근거 링크

- [환자용 복약정보 Receipt](https://github.com/AI-HealthCare-05/AH_05_04/blob/eaa1f396/docs/validation/rag/endpoints/LIST_PATIENT_MEDICATION_GUIDES.json)
- [제품 목록 Receipt](https://github.com/AI-HealthCare-05/AH_05_04/blob/eaa1f396/docs/validation/rag/endpoints/LIST_APPROVED_PRODUCTS.json)
- [Endpoint 검증 기록](https://github.com/AI-HealthCare-05/AH_05_04/blob/eaa1f396/docs/validation/rag/endpoints/README.md)
- [Source 계약](https://github.com/AI-HealthCare-05/AH_05_04/blob/eaa1f396/docs/contracts/targets/post-mvp-1/rag-source-ingestion-v1.md)
- [#165 합성 검증 경계](https://github.com/AI-HealthCare-05/AH_05_04/issues/165#issuecomment-5630479256)
- [#526 발급 정책 기록](https://github.com/AI-HealthCare-05/AH_05_04/issues/526#issuecomment-5662373888)

## 6. 공식 기록 반영 위치

- 본 문서를 #526 및 #166의 보류 근거로 연결한다.
- #526: 이번 Plan B 목적의 잔여 구현 보류, 차단 증빙, 재개 조건과 문서 링크를 연결한다. 이슈를 완료로 닫지 않는다.
- #166: QNT 완료 범위와 구분해 제품별 근거 RAG 인계의 후속 제약으로 링크한다.
- PR에는 문서 작성자와 담당 리뷰어를 명시하고, 제품/RAG 범위 확인과 기술적 차단 증빙을 구분해 검토받는다.
