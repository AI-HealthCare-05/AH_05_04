# Track B 과거 약 표시 경로 검토 자료

- 상태: **검토 경과 자료 · B안은 PR #474에서 리뷰용 구현 · 미병합** (2026-09-14 확인)
- 작성·Backend 구현 담당: 권가빈 (`hazelnutflavoured`)
- 책임 리뷰: 송은영 (`phina-io`) — Backend·소유권·조회 계약; 남한솔 (`solia142`) — Frontend 표시·연동
- 관련: [#202](https://github.com/AI-HealthCare-05/AH_05_04/issues/202), [#421](https://github.com/AI-HealthCare-05/AH_05_04/issues/421), [#468](https://github.com/AI-HealthCare-05/AH_05_04/pull/468), Frontend [#138](https://github.com/AI-HealthCare-05/AH_05_04/issues/138)

## 2026-09-14 후속 구현과 리뷰 경과

2026-09-12 작성 당시에는 A안을 우선 추천했고 새 API/DTO는 미구현이었다. 다음 날 같은 구현 담당자가 [PR #474](https://github.com/AI-HealthCare-05/AH_05_04/pull/474)에서 B안인 `GET /api/v1/medication-occurrences/{occurrence_id}/medication`을 리뷰용으로 구현했다. 따라서 A 우선 추천은 당시 비교 의견으로만 보존하며, 현재 후속 검토 대상은 #474의 B안이다.

#474의 [Decision 후보 `PD-202-HISTORY-20260913`](https://github.com/AI-HealthCare-05/AH_05_04/blob/ed118be9d8b71c8fdc1a07272450c3f86cfe5170/docs/governance/decisions/2026-09-13-track-b-occurrence-medication.md)는 단일 알림의 `occurrence_id`를 직접 사용하고 기존 날짜별 응답을 보존하기 위해 B안을 권장한다. 이 문서의 A/B 선택 승인을 기다려 확정 구현한 것이 아니라, 별도 Decision 후보·Proposed 계약과 구현을 함께 리뷰에 올린 경과다. 이를 사전 합의나 최종 선택 승인으로 소급 해석하지 않는다.

- 확인한 #474 HEAD: `ed118be9d8b71c8fdc1a07272450c3f86cfe5170`, 상태 `OPEN`, 미병합.
- 송은영의 [Backend 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/474#pullrequestreview-5193014401)는 2026-09-14 00:47:30 UTC, `f3694e2ff8128739f5aeea5ea720b494aa36bb4c` 대상이다. [#472 수정 요청](https://github.com/AI-HealthCare-05/AH_05_04/pull/472#issuecomment-5657540273)에는 Backend 승인 사실이 기록돼 있으나, 현재 조회한 리뷰 이벤트 상태는 `DISMISSED`다. 검토 내용과 이력은 보존하되 최신 HEAD의 유효한 승인으로 계산하지 않는다. 해제 이유는 확인되지 않았다.
- 남한솔의 Frontend 소비·인수 승인과 최종 병합은 확인되지 않았다. [Proposed 계약](https://github.com/AI-HealthCare-05/AH_05_04/blob/ed118be9d8b71c8fdc1a07272450c3f86cfe5170/docs/contracts/proposed/track-b-occurrence-medication-v1.md)의 승인·Current 승격은 해당 구현 PR에서 처리한다.

## 확인한 문제 (2026-09-12 기준)

처방 정정 후 예전 알림을 열면 과거 occurrence와 Check-in은 보존되지만, 현재 처방의 약 목록으로 그 occurrence의 약 이름을 찾을 수 없다. 현재 약을 배열 순서나 이름 유사도로 연결하면 정정된 약을 과거 기록에 잘못 표시할 수 있다.

기준 코드: develop `df259ab3ff4317edfff74857c09a29f63c36ab12`의 [날짜 조회 DTO](../../../backend/app/dtos/medication_schedules.py)와 [처방 조회 service](../../../backend/app/services/prescriptions.py). 날짜 DTO의 occurrence에는 version·version medication ID가 있고 약 이름·함량은 없다. 처방 latest와 detail은 모두 `_active_version_data`로 활성 버전을 반환한다.

다음은 리뷰 대기 중인 #468의 [합성 HTTP fixture](https://github.com/AI-HealthCare-05/AH_05_04/blob/c431b9f362862a7d32de387957b04b14952de034/docs/validation/track-b/issue-202-notification-handoff.json)에서 필드만 발췌한 값이다. 새 응답 형식이나 운영 데이터가 아니다.

| 응답·위치 | 버전 / 약 ID | 관측 값 |
| --- | --- | --- |
| `current_prescription.data.medications[0]` — 정정 전 | 약 `42c204ad-bb52-4227-bf57-45c4411ccab6` | `medication_name=합성테스트약`, `strength_text=null` |
| `current_prescription_after_correction.data` | 버전 `ad68d863-bf85-4c82-a2d3-14c3a300182a` | 현재 처방 revision 2 |
| 위 응답의 `medications[0]` | 약 `df9adbc9-675a-45df-a410-fd08ae5b3d21` | `medication_name=합성정정약`, `strength_text=null` |
| `historical_day.data.occurrences[0]` | 버전 `2b21e328-9c6f-45cf-a95a-6c0f9a4834b2`, 약 `42c204ad-bb52-4227-bf57-45c4411ccab6` | 2026-09-10 occurrence, `CLOSED`, Check-in `TAKEN`, revision 1 |

따라서 정정 후 날짜 조회만으로 과거 약 이름은 복구되지 않는다. UNCONFIRMED backlog에 포함된 약 이름도 일반적인 과거 조회를 대신하지 못한다. 해당 목록은 UNCONFIRMED 대상이며 TAKEN/NOT_TAKEN으로 정정되면 목록에서 빠진다. 이전 기기의 캐시나 localStorage 역시 재로그인·다른 기기에서 사용할 정본이 아니다.

## 당시 선택안 비교와 후속 판단

| 안 | 조회 방식 | 비용·제약 | 판단 |
| --- | --- | --- | --- |
| A | 날짜 조회 occurrence에 해당 버전의 최소 약 표시 정보를 포함 | 기존 DTO 변경·항목별 정보 반복. 서버가 occurrence의 불변 version medication을 조회하므로 별도 DB snapshot 복제는 불필요 | 2026-09-12 당시 우선 추천. 한 응답으로 과거·현재 약을 구분할 수 있으나 #474는 기존 목록 응답 보존을 위해 B안을 제안 |
| B | 소유한 occurrence 또는 version medication의 최소 표시 정보를 별도 조회 | 새 route·DTO·소유권 검사·추가 요청과 로딩/실패 처리 필요 | #474에서 단일 알림의 원래 약 연결을 위해 리뷰용 구현. 최종 승인은 해당 PR에서 확인 |

전체 처방 버전 history API는 이 문제에 필요한 범위보다 넓다. 별도 제품 요구가 없다면 이번 선택에서 제외한다. A의 새 필드 이름·requiredness·nullable 정책이나 B의 URL·오류 코드는 이 문서에서 확정하지 않는다.

## #474에서 남은 리뷰·인수 사항

1. 송은영: #474 최신 HEAD의 GET·DTO·immutable version medication 연결·SELF 소유권과 읽기 불변성을 검토하고 유효한 승인 증빙을 남긴다.
2. 남한솔: #474가 제공하는 약명·함량·1회 복용량/단위와 nullable 표현, 알림·과거 기록 화면의 추가 조회·로딩·실패 처리 및 fixture 인수를 확인한다.
3. 양 도메인: 구체적인 경로·필드·실패 의미는 #474의 Decision·Proposed 계약·OpenAPI/DTO·구현·테스트를 함께 검토한다. 이 문서 PR의 승인으로 해당 계약 승인을 대체하지 않는다.

#474는 occurrence → schedule → 원래 version medication 및 SELF 소유권을 조회하며, 기존 날짜별 DTO·DB schema는 변경하지 않는다. 이 문서는 새 공유 계약을 별도로 정의하거나 OCR 원문·환자 메타데이터의 추가 전송을 제안하지 않는다.

## 검증 증빙과 남은 확인

#474의 [검증 기록](https://github.com/AI-HealthCare-05/AH_05_04/blob/ed118be9d8b71c8fdc1a07272450c3f86cfe5170/docs/validation/track-b/issue-202-closure-readiness.md)과 [합성 HTTP fixture](https://github.com/AI-HealthCare-05/AH_05_04/blob/ed118be9d8b71c8fdc1a07272450c3f86cfe5170/docs/validation/track-b/issue-202-occurrence-medication.json)가 Backend 검증 증빙이다. 아래 항목은 해당 증빙과 대조할 인수 기준이며, 이번 문서 작업에서 테스트를 재실행했다는 뜻이 아니다. Frontend UI/E2E·재접속 검증은 별도로 남아 있다.

- 정정 전후 약 이름·함량이 다른 경우에도 과거 occurrence는 이전 version medication의 값을 표시한다.
- TAKEN·NOT_TAKEN·UNCONFIRMED 모두 같은 과거 약을 식별하며 backlog 포함 여부에 의존하지 않는다.
- 같은 이름의 서로 다른 약 행도 ID로 구분하고, 함량 null을 임의 값으로 채우지 않는다.
- 존재하지 않거나 다른 사용자가 소유한 ID, 로그아웃·계정 전환·재접속에서 잘못된 약 정보가 노출되지 않는다.
- 응답의 `no-store`, 계약과 OpenAPI의 필드·nullable 일치, Frontend의 로딩·실패 표시를 확인한다.

#468은 현재 동작을 관측한 인계 증빙이다. 그 PR의 테스트 통과를 위 선택안의 승인이나 새 표시 경로의 구현 완료로 해석하지 않는다.
