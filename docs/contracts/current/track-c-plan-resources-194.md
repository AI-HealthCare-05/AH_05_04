# Track C 계획별 안내·화면 연결 자료 — #194 / #139

- 상태: Current — PR #639의 구현과 함께 반영하는 런타임 계약. 최종 책임 리뷰·병합 대기, 외부 공개 승인 아님.
- 구현 담당: 권가빈 (@hazelnutflavoured).
- 단일 책임 리뷰어: 김지혜 (@Jye-rookie), Backend·Frontend 소비·소유권·과거 문구·상태 확인.
- Frontend 소비 검토: 남한솔. #629와 #635는 별도 선행 리뷰 PR이며 이 문서가 그 승인을 대신하지 않는다.
- 근거: [PD-194-3](../../governance/decisions/2026-09-16-track-c-travel-situation-194.md)의 추가 범위.

## 구현·검증 및 반영 상태

- [PR #639](https://github.com/AI-HealthCare-05/AH_05_04/pull/639)에 Router·DTO·Service·Repository와 통합 테스트를 포함한다. 기존 테이블을 사용하며 새 migration은 없다.
- 구현 HEAD `b118a71a`의 [CI](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/35065376564)는 10개 lane 모두 SUCCESS다. [검증 기록](../../testing/track-c-support-routes-194.md)을 따른다.
- 김지혜의 [책임 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/639#pullrequestreview-5219449254)는 코드의 추가 결함이 없음을 확인하고, 같은 구현 PR에서 Current 이동과 상태·참조 정렬을 요청했다.
- 이 이동은 해당 요청의 반영이다. #629·#635는 병합됐으며 최신 develop 통합 후 정리된 diff의 최종 책임 리뷰는 대기 중이다. 병합 전 develop의 동작이나 #194 전체 완료로 해석하지 않는다.
- 약별 Citation·증상별 임상 정책·실제 Push 도착·외부 공개 승인은 별도이며 공개 게이트를 유지한다.

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

FORGOT→REMINDER_SETUP 화면은 Copy 버전에 관계없이 기기 알림 상태를 명시적으로 확인한다.
`inspectWebPushState`는 현재 Notification.permission, 저장된 binding, 기존 service worker의
getSubscription만 읽는다. 권한 요청·service worker 등록·구독 생성/삭제·서버 쓰기는 하지 않는다.
로컬 granted는 서버 유효성이나 실제 도착을 증명하지 않는다. 구독 재조정은 기존 설정 화면에서 수행한다.
상태 확인 전에는 완료를 막고, granted일 때는 일정·알림 확인 문구를 사용한다.
그 외 상태는 “알림 설정 없이 복약 일정만 확인했어요”에 직접 동의하면 완료할 수 있다.
완료 직전 읽기 전용 재확인 결과가 바뀌면 저장을 중단하고 변경된 문구에 다시 동의받는다.
서버 PATCH는 기존 사용자 confirmed만 기록하며 알림 설정 성공이나 물리적 기기 상태를 증명하지 않는다.
일정 변경의 완료 기준은 기존 일정 확인/저장 후 명시 완료다.
약 챙기기는 과거 `track-c-support-copy-ko-2026-09-15.1`만 기존 의미를 유지하며,
그 이후 Copy에서도 준비 확인 문구를 유지한다. 신규 버전과 활성 API fixture로 회귀를 검증한다.

자료 조회의 네트워크 오류·503은 안내와 연결 자료를 숨기되 Plan 상태 표시·취소·자료 재조회를 허용한다.
안내가 복구되기 전 완료는 막는다. 401은 로그인으로 이동하며 403/404/409는 전체 흐름을 중단한다.

복용법·필요성 사유의 ‘현재 일정’은 사용자 설정 정보이며 승인된 복용법·효능 근거를 대신하지 않는다.
현재 Guide content에 Citation을 붙이거나 완성된 약별 근거로 표시하지 않는다.
증상 발생·불확실 선택 시 Frontend는 일반 안내·완료 버튼을 중단하며 임의 symptom_code나 긴급도 판정을 저장하지 않는다.
서버 임상 분기 구현 완료가 아니며, 안전 확인을 다시 진행할 때 기존 API 충돌을 우회하지 않는다.

## 검증 대상

`test_track_c_plan_resources.py`는 원래 약 결속, SELF 404, 읽기 전용, 과거 Copy 복원·실패를 검증한다.
`TrackCPage.test.tsx`는 사유별 링크·근거 부재 표시·Safety 중단·Push 상태와 완료 직전 재확인을 검증한다.
실제 기기 Push 도착과 약별 Source/임상 규칙 검증은 이 테스트 범위가 아니다.
