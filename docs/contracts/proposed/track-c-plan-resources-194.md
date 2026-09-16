# Track C 계획별 안내·화면 연결 자료 — #194 / #139

- 상태: Proposed / 구현·기술 리뷰 대상. 전체 사유의 완료·의료 안전 승인·공개 승인이 아니다.
- 구현 담당: 권가빈 (@hazelnutflavoured).
- 단일 책임 리뷰어: 김지혜 (@Jye-rookie), Backend·Frontend 소비·소유권·과거 문구·상태 확인.
- Frontend 소비 검토: 남한솔. #629와 #635는 별도 선행 리뷰 PR이며 이 문서가 그 승인을 대신하지 않는다.
- 근거: [PD-194-3](../../governance/decisions/2026-09-16-track-c-travel-situation-194.md)의 추가 범위.

## GET /api/v1/support-action-plans/{id}/resources

operationId: `support-action-plan.resources.get`. 인증된 SELF 소유권과 기존 부모 관계로만 조회한다.
Plan → Barrier → Check-in → occurrence → schedule → version medication → prescription의
동일 소유권을 검사한다. ID를 클라이언트에게 받아 관계를 대체하거나 최신 처방의 유사 약으로 바꾸지 않는다.

200의 data 필수 필드:

| 필드 | 타입·의미 |
| --- | --- |
| support_action_plan_id | UUID, 요청한 Plan |
| barrier_code | 기존 BarrierCode, 해당 Plan의 원래 사유 |
| occurrence_id | UUID, 원래 복약 기록 |
| occurrence_local_date | date, 원래 기록의 scheduled_local_date |
| prescription_version_medication_id | UUID, 원래 약 항목 |
| support_copy | 기존 SupportCopyData: title/body/confirmation_prompt/primary_label/secondary_label |

원래 관계는 한 SQL snapshot으로 읽는다. Copy는 저장된 rule_version/copy_version의 승인 allowlist와
파일을 검증한 뒤 읽으며 활성 버전으로 대체하지 않는다. 이는 정적 승인 안내이며 약별 Citation이나
증상별 임상 판정 자료가 아니다. 조회는 ACTIVE/COMPLETED/CANCELLED 모두 허용하고 mutation·lock·멱등 기록을 만들지 않는다.

- 401: 기존 인증 오류.
- 404 ACTION_PLAN_NOT_FOUND: 미존재·타인 동일 응답.
- 422 VALIDATION_FAILED: 잘못된 경로 UUID.
- 503 SUPPORT_CONFIG_UNAVAILABLE: 과거 Rule·Copy를 안전하게 복원할 수 없음.
- 기존 오류 envelope와 Cache-Control: no-store 적용.
- 기존 Plan GET/POST/PATCH 응답, DB schema와 transaction 순서는 변경하지 않는다.

## 사유별 화면 연결

| 사유 | 이번에 연결하는 범위 | 남은 범위 |
| --- | --- | --- |
| 잊음 | 원래 약의 일정 화면, #470의 기기 알림 설정 화면, 설정 상태 명시 조회 | 실제 기기 도착 검증·공개 승인 |
| 일정 변경·외출 | [두 상황 분기](track-c-travel-situation-194.md) | 선행 PR 승인·최종 인수 |
| 복용법 의문 | 원래 약 정보·일정 화면, 저장 당시 정적 안내, 약별 설명·근거 없음 표시 | 승인된 약별 설명/Citation 실제 연결 |
| 필요성 의문 | 원래 약 정보, 저장 당시 정적 안내, 약별 목적·근거 없음 표시 | 승인된 복용 목적/Citation 실제 연결 |
| 약에 대한 걱정 | 저장 당시 승인 안내, 증상 발생·불확실 시 일반 흐름 중단 | 의료 검토를 마친 증상별 Safety 정책·문구 연결 |
| 비용·접근 | 기존 정적 안내 유지 | 재고 조회·연락처 연결은 사용자 결정으로 제외 |

새 Copy 2026-09-16.1의 FORGOT→REMINDER_SETUP 화면은 기기 알림 상태를 명시적으로 확인한다.
상태 확인은 #470의 기존 getWebPushState를 재사용하며 기존 구독의 서버 재확인/철회 정리가 발생할 수 있다.
미지원·거절·실패·등록 없음은 설정 성공이 아니다. granted 확인 후에만 완료 확인 버튼을 열고,
완료 저장 직전 다시 확인한다. 시간차에 따른 실제 수신을 보장하지 않는다.
서버 PATCH는 기존 사용자 confirmed를 받으며 물리적 기기 상태를 증명하지 않는다.
과거 Copy 계획과 일정 변경의 완료 기준은 기존 일정 확인/저장 후 명시 완료다.

복용법·필요성 사유의 ‘현재 일정’은 사용자 설정 정보이며 승인된 복용법·효능 근거를 대신하지 않는다.
현재 Guide content에 Citation을 붙이거나 완성된 약별 근거로 표시하지 않는다.
증상 발생·불확실 선택 시 Frontend는 일반 안내·완료 버튼을 중단하며 임의 symptom_code나 긴급도 판정을 저장하지 않는다.
서버 임상 분기 구현 완료가 아니며, 안전 확인을 다시 진행할 때 기존 API 충돌을 우회하지 않는다.

## 검증 대상

`test_track_c_plan_resources.py`는 원래 약 결속, SELF 404, 읽기 전용, 과거 Copy 복원·실패를 검증한다.
`TrackCPage.test.tsx`는 사유별 링크·근거 부재 표시·Safety 중단·Push 상태와 완료 직전 재확인을 검증한다.
실제 기기 Push 도착과 약별 Source/임상 규칙 검증은 이 테스트 범위가 아니다.
