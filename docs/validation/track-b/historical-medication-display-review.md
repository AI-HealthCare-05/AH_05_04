# Track B 과거 약 표시 경로 검토 자료

- 상태: **검토 자료 · 선택 미승인 · 새 API/DTO 미구현** (2026-09-12)
- 작성·Backend 구현 담당: 권가빈 (`hazelnutflavoured`)
- 책임 리뷰: 송은영 (`phina-io`) — Backend·소유권·조회 계약; 남한솔 (`solia142`) — Frontend 표시·연동
- 관련: [#202](https://github.com/AI-HealthCare-05/AH_05_04/issues/202), [#421](https://github.com/AI-HealthCare-05/AH_05_04/issues/421), [#468](https://github.com/AI-HealthCare-05/AH_05_04/pull/468), Frontend [#138](https://github.com/AI-HealthCare-05/AH_05_04/issues/138)

## 확인한 문제

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

## 비교할 선택안

| 안 | 조회 방식 | 비용·제약 | 판단 |
| --- | --- | --- | --- |
| A | 날짜 조회 occurrence에 해당 버전의 최소 약 표시 정보를 포함 | 기존 DTO 변경·항목별 정보 반복. 서버가 occurrence의 불변 version medication을 조회하므로 별도 DB snapshot 복제는 불필요 | **현재 날짜별 기록 화면에는 우선 추천**. 한 응답으로 과거·현재 약을 구분할 수 있음 |
| B | 소유한 occurrence 또는 version medication의 최소 표시 정보를 별도 조회 | 새 route·DTO·소유권 검사·추가 요청과 로딩/실패 처리 필요 | 여러 실제 화면이 같은 조회를 필요로 하면 검토 |

전체 처방 버전 history API는 이 문제에 필요한 범위보다 넓다. 별도 제품 요구가 없다면 이번 선택에서 제외한다. A의 새 필드 이름·requiredness·nullable 정책이나 B의 URL·오류 코드는 이 문서에서 확정하지 않는다.

## 리뷰에서 결정할 사항

1. 남한솔: 날짜별 기록·알림 진입 화면에서 필요한 과거 약 표시가 이름과 함량만으로 충분한지, 추가 조회를 필요로 하는 실제 화면이 있는지 확인한다.
2. 송은영: A/B 중 조회 위치와 immutable version medication 연결·SELF 소유권 검증 범위를 검토한다.
3. 양 도메인: 선택 후 필드·nullable·조회 실패 의미를 Decision과 상태에 맞는 계약 문서로 확정한다. 이후 구현 PR에서 OpenAPI/DTO·구현·Frontend 소비·계약/통합 검증을 정렬한다.

검토 시 최소 후보는 기존 명칭인 `medication_name`, `strength_text`다. 필요한 표시 범위를 넘는 OCR 원문·환자 메타데이터는 추가하지 않는다. 소유권은 occurrence → schedule/version → prescription → SELF chain으로 확인하고, 다른 사용자의 리소스 존재 여부를 드러내지 않으며, 민감 응답의 `no-store` 경계를 유지하는 안을 검토한다. 구체적인 실패 응답과 UI 문구는 후속 합의 대상이다.

## 선택 후 필요한 검증

아래는 **후속 검증 계획**이며 이번 문서 작업에서 실행·통과한 테스트가 아니다.

- 정정 전후 약 이름·함량이 다른 경우에도 과거 occurrence는 이전 version medication의 값을 표시한다.
- TAKEN·NOT_TAKEN·UNCONFIRMED 모두 같은 과거 약을 식별하며 backlog 포함 여부에 의존하지 않는다.
- 같은 이름의 서로 다른 약 행도 ID로 구분하고, 함량 null을 임의 값으로 채우지 않는다.
- 존재하지 않거나 다른 사용자가 소유한 ID, 로그아웃·계정 전환·재접속에서 잘못된 약 정보가 노출되지 않는다.
- 응답의 `no-store`, 계약과 OpenAPI의 필드·nullable 일치, Frontend의 로딩·실패 표시를 확인한다.

#468은 현재 동작을 관측한 인계 증빙이다. 그 PR의 테스트 통과를 위 선택안의 승인이나 새 표시 경로의 구현 완료로 해석하지 않는다.
