# Product Decision 후보: occurrence 원래 약 표시 조회

| 항목 | 값 |
| --- | --- |
| Decision ID | PD-202-HISTORY-20260913 |
| 상태 | Proposed · 리뷰용 구현 완료 · 승인 대기 |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 책임 리뷰 | 송은영 (`phina-io`) — Backend/API·Security; 남한솔 (`solia142`) — Frontend 소비 |
| 배정 근거 | [#202](https://github.com/AI-HealthCare-05/AH_05_04/issues/202)의 명시 배정 |
| 계약 | [occurrence 약 표시 v1](../../contracts/proposed/track-b-occurrence-medication-v1.md) |
| 기준 | develop f56a6322 및 #468 head 0f068b72, 2026-09-13 조회 |

## 감사 결과

- [Occurrence DTO](../../../backend/app/dtos/medication_schedules.py)는 version·약 ID를 반환하지만 약 표시 필드가 없다.
- [처방 서비스](../../../backend/app/services/prescriptions.py)의 상세/latest는 `_active_version_data`로 활성 version만 반환한다.
- [처방 라우터](../../../backend/app/apis/v1/prescription_routers.py)에는 과거 version 지정 GET이 없다.
- [약 모델](../../../backend/app/models/prescriptions.py)의 PrescriptionVersionMedication에는 당시 확정 약명·함량·용량이 이미 보존된다.
- [기존 소유권 조회](../../../backend/app/repositories/medication_schedule_repository.py)의 get_occurrence_owned는 SELF parent chain을 확인한다. 이 경계를 재사용하거나 동일 chain의 명시적 join으로 조회할 수 있다.
- [#468](https://github.com/AI-HealthCare-05/AH_05_04/pull/468)의 HTTP 인계 자료는 실제 처방 정정 후 과거 약 ID와 현재 medications가 불일치함을 재현한다. 이 PR은 감사 시 OPEN이며 병합 증빙으로 쓰지 않았다.

## 선택 요청

| 안 | 영향 | 판단 |
| --- | --- | --- |
| 기존 latest/상세만 사용 | 새 API 없음 | 과거 약을 돌려주지 못해 요구 충족 불가 |
| 날짜별 occurrence DTO에 약 필드 추가 | 기존 목록 응답·모든 소비자 변경 | 전체 목록에 약 표시가 필요한 경우 유효하나 이번 단일 기록 연결보다 범위가 넓음 |
| 과거 처방 version 전체 상세 GET | 별도 version 조회·전체 약 목록 제공 | 범용 처방 이력 요구가 있으면 유효하나 이번 연결에는 불필요한 약까지 조회 |
| occurrence 한 건의 원래 약 표시 GET | 새 GET·작은 DTO·소유권 조회와 테스트 | **권장 후보**. 알림의 occurrence_id를 직접 사용하고 기존 응답을 보존 |

새 API와 DTO를 추가하므로 공유 계약 변경이다. 구현 유지보수 비용은 GET 1개, 작은 DTO,
명시적 Service/Repository 조회와 계약·통합 테스트다. 새 테이블·migration·dependency·event bus는
제안하지 않는다. 약 snapshot 수정, 처방 정정 flow 변경, AI/Worker/Frontend 구현은 범위 밖이다.

## 승인할 delta

- D1: 계약의 GET 경로와 조회 가능 상태(비활성 version·취소 occurrence 포함).
- D2: 세 ID, 약명·함량·1회 복용량/단위의 최소 필드 및 nullable 표현.
- D3: SELF parent chain, 기존 404 의미 재사용, 읽음·Check-in 등 모든 write 없음.
- D4: 알림의 원래 날짜·occurrence·version 약 ID 연결과 표시 실패 처리 인계.

송은영은 D1–D3, 남한솔은 D1·D2·D4를 검토한다. 승인 evidence는 대상 commit·review URL·시각과
함께 기록한다. 현재 이 delta의 승인 증빙은 없다. 리뷰어를 변경하는 경우 이 작업의 영향 영역을 명시해
#202와 구현 PR의 담당을 함께 정렬한다.

이 delta와 함께 GET·DTO·소유권 조회·실제 HTTP 테스트 및 별도 합성 인계 fixture를 준비했다. 이는 도메인 리뷰
승인을 대신하지 않는다. #468 파일은 수정하지 않고 같은 기존 합성 seed를 재사용한다.
이 후보 문서 작성은 #202 종료, Frontend E2E 통과 또는 Production 공개를 의미하지 않는다.
