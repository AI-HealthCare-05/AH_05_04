# PD-202-HISTORY-20260913 — occurrence 원래 약 표시 조회

| 항목 | 값 |
| --- | --- |
| Decision ID | PD-202-HISTORY-20260913 |
| 상태 | Approved · Current runtime 반영 완료 |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 책임 리뷰 | 송은영 (`phina-io`) — Backend/API·Security; 남한솔 (`solia142`) — Frontend 소비 |
| 배정 근거 | [#202](https://github.com/AI-HealthCare-05/AH_05_04/issues/202)의 명시 배정 |
| 계약 | [occurrence 약 표시 v1](../../contracts/current/track-b-occurrence-medication-v1.md) |
| 구현 병합 | PR #474 · `9d8239ff1886327af154db53981ce839191da879` |
| 승인 근거 | [Backend 최종 승인](https://github.com/AI-HealthCare-05/AH_05_04/pull/474#pullrequestreview-5193439578) · [Frontend 인수 승인](https://github.com/AI-HealthCare-05/AH_05_04/issues/202#issuecomment-5658885279) |

## 최초 감사 결과와 채택

- 기존 날짜별 [Occurrence DTO](../../../backend/app/dtos/medication_schedules.py)는 version·약 ID를 반환하지만 약 표시 필드가 없다.
- [처방 서비스](../../../backend/app/services/prescriptions.py)의 상세/latest는 `_active_version_data`로 활성 version만 반환한다.
- [처방 라우터](../../../backend/app/apis/v1/prescription_routers.py)에는 과거 version 지정 GET이 없어, occurrence 단건의 원래 약 조회 경로를 별도로 채택했다.
- [약 모델](../../../backend/app/models/prescriptions.py)의 PrescriptionVersionMedication에는 당시 확정 약명·함량·용량이 이미 보존된다.
- [기존 소유권 조회](../../../backend/app/repositories/medication_schedule_repository.py)의 get_occurrence_owned는 SELF parent chain을 확인한다. 이 경계를 재사용하거나 동일 chain의 명시적 join으로 조회할 수 있다.
- [#468](https://github.com/AI-HealthCare-05/AH_05_04/pull/468)의 HTTP 인계 자료는 실제 처방 정정 후 과거 약 ID와 현재 medications가 불일치함을 재현했고, 후속 #474가 이를 조회하는 Current API를 구현했다.

## 선택 결과

| 안 | 영향 | 판단 |
| --- | --- | --- |
| 기존 latest/상세만 사용 | 새 API 없음 | 과거 약을 돌려주지 못해 요구 충족 불가 |
| 날짜별 occurrence DTO에 약 필드 추가 | 기존 목록 응답·모든 소비자 변경 | 전체 목록에 약 표시가 필요한 경우 유효하나 이번 단일 기록 연결보다 범위가 넓음 |
| 과거 처방 version 전체 상세 GET | 별도 version 조회·전체 약 목록 제공 | 범용 처방 이력 요구가 있으면 유효하나 이번 연결에는 불필요한 약까지 조회 |
| occurrence 한 건의 원래 약 표시 GET | 새 GET·작은 DTO·소유권 조회와 테스트 | **채택**. 알림의 occurrence_id를 직접 사용하고 기존 응답을 보존 |

공유 계약 변경은 GET 1개, 작은 DTO, 명시적 Service/Repository 조회와 계약·통합 테스트로 한정했다.
새 테이블·migration·dependency·event bus, 약 snapshot 수정과 처방 정정 flow 변경은 포함하지 않는다.

## 승인된 delta

- D1: 계약의 GET 경로와 조회 가능 상태(비활성 version·취소 occurrence 포함).
- D2: 세 ID, 약명·함량·1회 복용량/단위의 최소 필드 및 nullable 표현.
- D3: SELF parent chain, 기존 404 의미 재사용, 읽음·Check-in 등 모든 write 없음.
- D4: 알림의 원래 날짜·occurrence·version 약 ID 연결과 표시 실패 처리 인계.

송은영은 D1–D3를 #474 최종 HEAD에서 승인했고, 남한솔은 D1·D2·D4와 합성 fixture를 #474 병합본 기준으로 인수 승인했다. #202 본문은 현재 PR 책임 리뷰어 1명 기준과 Frontend 소비 인수를 별도 조건으로 구분하며, 두 조건 모두 충족됐다.

GET·DTO·소유권 조회·실제 HTTP 테스트 및 합성 인계 fixture는 #474에 포함되어 develop에 병합됐다. #202는 Backend 승인과 Frontend 인수 후 종료됐다. Production 공개는 이 계약 승격과 별도다.
