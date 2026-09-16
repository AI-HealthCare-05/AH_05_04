# #196 Track C 공용 RAG 연결 준비 상태

- 확인일: 2026-09-16; 기준 develop `a437a7d4`.
- 상태: **BLOCKED — 실제 RAG Handler 연결 미구현**. #193 내부 데모 요청은 이 공백을 승인하지 않는다.
- #196 구현 담당: @Jye-rookie; 단일 책임 리뷰어: @ceohwj — RAG·Citation·Validator.
- 전문 증빙: @phina-io (Backend·Transaction·Security), 소비 확인 @solia142.
- 이번 작업: 선행조건 확인과 근거 기록. 공용 구현·공개 DTO·Provider·Source 데이터는 변경하지 않는다.

## 확인 결과

| 선행조건 | 확인 근거 | 결과 |
| --- | --- | --- |
| #125 Freeze·Authority Receipt | [PD-125](../governance/decisions/2026-08-31-rag-p0-contract-freeze.md), GitHub #125 CLOSED | Approved Target와 version/hash 기록 있음. 외부 원본 bundle hash를 이번에 다시 계산한 것은 아님 |
| 공용 Retrieval | [evidence_retrieval](../../ai_worker/tasks/rag/evidence_retrieval.py), [retrieval_runtime](../../ai_worker/tasks/rag/retrieval_runtime.py) | kernel 있음. 첫 모듈은 명시적으로 non-authoritative synthetic-first candidates |
| 공용 Generator/Output·Track C handoff | [#180](https://github.com/AI-HealthCare-05/AH_05_04/issues/180), [Runtime target](../contracts/targets/post-mvp-1/rag-runtime-v1.md) | 재사용 가능한 Track C Generator/handoff 구현·Receipt 확인 못함. 임의 Protocol로 빈 부분을 채우지 않음 |
| Citation kernel | [claim validator](../../ai_worker/tasks/rag/claim_citation_validator.py), [authorization](../../ai_worker/tasks/rag/citation_authorization.py), [handoff](../../ai_worker/tasks/rag/guide_evidence_handoff.py) | 구조 검증은 있음. handoff가 권위·승인 진위를 검증하지 않는다고 명시하고 endpoint-member 계약 차단도 남음 |
| Source Snapshot·Knowledge | [#634](https://github.com/AI-HealthCare-05/AH_05_04/issues/634) OPEN | materialization 구현·실제 Source/서버 인계는 분리. #591/#593/#613 증빙 이전 실제 연결 차단 |
| Runtime Bundle | [builder](../../ai_worker/tasks/rag/runtime_bundle_builder.py), [#181](https://github.com/AI-HealthCare-05/AH_05_04/issues/181) OPEN | BUILDING kernel은 READY·활성 승인을 반환하지 않음. Track C가 사용할 exact 승인 bundle/manifest 미확보 |
| Track C Citation·Fallback 소비 | [support DTO](../../backend/app/dtos/track_c_support.py), [#186](https://github.com/AI-HealthCare-05/AH_05_04/issues/186) | 현재 정적 지원 DTO는 검증된 RAG Claim·Citation 저장/응답 계약이 아님. #186도 실제 Citation DTO 없으면 차단 유지 |
| 현재 안전·지원 경계 | [flow](../../backend/app/services/track_c_flow.py), [support service](../../backend/app/services/track_c_support.py) | ROUTINE만 일반 지원. 정적 versioned copy 사용; 별도 Track C Retriever·Generator 없음 |

Issue가 OPEN이라는 사실만으로 차단한 것이 아니다. 실제 kernel 경계, 소비 DTO,
Authority와 Source/Bundle Receipt의 부재가 연결을 막는다. 확인하지 않은 승인 artifact가
존재하지 않는다고 단정하지 않으며, 인계되면 exact version/hash를 대조해 재평가한다.

## 구현 재개에 필요한 인계

1. @ceohwj: 실제 호출할 공용 Generator·Output Validation 및 Retrieval/Citation 입력·출력,
   버전·구현 위치·합성 실행 Receipt. Track C Barrier·확정 처방·Source filter 매핑 확정.
2. @ceohwj / Source 담당: 목적·환경 적격 Source Snapshot과 Runtime Bundle ID·manifest hash,
   freshness·revocation·Citation authorization 증빙.
3. @ceohwj / @phina-io / @solia142: 검증 결과를 Support Offer와 과거 Plan에 결속하는 저장·DTO,
   Citation·근거 부족·고정 Fallback·Safety CTA·message/copy version 계약 확정.
4. 구현 PR에서 non-ROUTINE·Template·Constraint의 Provider 호출 0건, 근거 없음·상충·비활성·timeout·
   Citation/schema/Safety 실패의 결과 폐기와 고정 fallback, flag OFF·PII sentinel·멱등/currentness 회귀 실행.

## 이번 데모에서의 동작

#194의 기존 정적 안내와 원래 약/일정 연결을 그대로 사용한다. 이를 #196의 RAG fallback이나
약별 근거 설명으로 이름만 바꾸지 않는다. 검증되지 않은 Guide 평문, 가짜 Citation,
새 Track C RAG pipeline을 추가하지 않는다. #196은 완료·Close 대상이 아니다.
`PUBLIC_TRACK_C`·`PUBLIC_TRACK_F`와 Privacy 공개 게이트를 해제하지 않는다.
