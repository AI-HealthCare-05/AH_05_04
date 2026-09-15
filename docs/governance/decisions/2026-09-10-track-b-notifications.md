# PD-203 — 앱 내부 알림과 재알림 상세 계약

| 항목 | 값 |
| --- | --- |
| 상태 | Approved · Current runtime 반영 완료 |
| 기준일 | 2026-09-10 |
| Issue | [#203](https://github.com/AI-HealthCare-05/AH_05_04/issues/203) |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 기술 리뷰 | 송은영 (`phina-io`) — Track B Backend·DB·Security |
| 소비 계약 리뷰 | 남한솔 (`solia142`) — Frontend |
| 현재 계약 | [Notification 계약 v1](../../contracts/current/track-b-notifications-v1.md) |

## 문제와 승인 경계

#202는 Schedule·Occurrence·Check-in API를 구현하며, #203은 Notification 저장·목록·읽음·재알림을 담당한다. 최초 조사 기준 develop `99bb259`에는 Notification 모델·API가 없었으나, 구현 PR #430이 병합되고 #202의 연계 API도 후속 병합됐다. 리뷰어 지정 자체가 아니라 아래 실제 리뷰·인수·병합 기록을 승인 근거로 사용한다.

Approved Contract Freeze v4와 원본 `FinalProject Documents/04_Decision/track-b-adherence-v1.md`는 `notification_record`의 최소 필드, 사용자 요청 재알림 1회, 멱등성, 활성 재알림 중복 오류를 정한다. #430은 `kind`·`status`, 읽음 필드, 목록·읽음 API, 재알림 시간 입력과 허용 상태를 구현·검증했고 지정 역할의 리뷰와 병합을 거쳐 Current 계약의 근거가 됐다.

## 확정 원본과 오래된 요약의 차이

처방 version 활성화의 transaction owner와 B 동기 취소 port는 원본 Freeze v4의 처방 변경 절 및 저장소 [처방 버전 목표 계약](../../contracts/targets/post-mvp-1/prescription-version-v1.md)에 이미 명시돼 있다. 같은 transaction에서 `effective_at` 이후 이전 version의 `PENDING` occurrence와 미전달 알림을 취소하며 Outbox나 사후 보상을 사용하지 않는다.

최초 조사 당시 [Check-in 목표 계약](../../contracts/targets/post-mvp-1/checkin-v1.md)의 transaction 결합 미정 문구와 [테스트 전략](../../testing.md)의 후속 Decision 대기 문구가 위 원본과 불일치했다. #415 기술 리뷰에서 원본과의 차이가 확인되어 #203 구현 PR에서 두 요약을 동기화했다. 새로운 transaction 방식을 선택한 것이 아니다.

## 확정 결정

1. 기존 최소 모델 `notification_record` 하나를 사용한다. 외부 Message·Recipient·전송 큐를 신설하지 않고 SELF 소유권은 occurrence parent chain에서 확인한다.
2. 최초 알림과 재알림을 구분하고 전달 상태와 읽음 여부를 별도로 저장한다. 앱 내부 목록에 게시하는 DB 전이를 전달로 정의한다. HTTP GET은 상태를 바꾸지 않는다.
3. 최초 알림은 occurrence의 기존 예정 시각을 사용한다. 재알림은 사용자가 명시한 미래 시각을 받아 확인 기한 안에서 1회만 생성한다. 알림 읽음·취소에서 복약 결과를 만들거나 수정하지 않는다.
4. 알림 생성·게시·취소는 occurrence 잠금 뒤 Notification row를 처리한다. 처방 변경은 이미 승인된 B 취소 port에 같은 session의 구현을 주입한다.
5. 2026-09-10 권가빈의 [제품 결정](https://github.com/AI-HealthCare-05/AH_05_04/pull/415#issuecomment-5615835554)에 따라 Track C `REMINDER_SETUP`은 향후 복약 일정 확인·설정 흐름으로 연결하고 기존 `PUT /api/v1/prescription-version-medications/{prescription_version_medication_id}/schedule`을 사용한다. occurrence 단위 재알림 POST는 재사용하지 않는다. 따라서 Frontend가 CLOSED + NOT_TAKEN occurrence에 재알림 POST를 호출해야 하는 충돌은 해소되며, 일반 Track B 재알림의 PENDING 전용 조건은 유지한다.
6. 알림 응답은 원본 occurrence의 `occurrence_local_date`를 제공한다. Frontend는 재알림 시각에서 날짜를 추정하지 않고, occurrence ID와 #202의 원래 약 조회 DTO를 검증한 뒤 기록 화면으로 이동한다.

새 테이블은 알림의 게시·읽음·취소를 Check-in과 독립적으로 보존하기 위해 필요하다. occurrence에 필드를 추가하는 대안은 최초 알림과 재알림을 별도로 보존하지 못한다. 외부 전송 infrastructure는 이번 범위에 필요하지 않다. 추가 비용은 모델·migration 1개, Repository·Service·API 및 생성·게시 명령과 테스트다.

## 승인 및 완료 근거

- Backend 책임 리뷰: 송은영이 PR #430 최종 HEAD `b470eee98f27500d343168b066fa9c96e43c7a74`를 [APPROVED](https://github.com/AI-HealthCare-05/AH_05_04/pull/430#pullrequestreview-5176496219)했다.
- Frontend 소비 리뷰: 남한솔이 Notification DTO·`occurrence_local_date`·404/409·Track C 분리 경계를 [승인](https://github.com/AI-HealthCare-05/AH_05_04/pull/430#pullrequestreview-5173983820)했다. 승인 이후 계약/API/DTO 변경은 없고 최종 작성자 변경은 migration 부모와 검증 기록 정합화뿐이었다.
- 구현 병합: PR #430은 merge SHA `8010dfce7fda434c276ce5ca394e72f4a0463a53`로 develop에 병합됐고 #203은 종료됐다.
- #202 연계: PR #474가 merge SHA `9d8239ff1886327af154db53981ce839191da879`로 병합됐으며, 송은영의 [Backend 최종 승인](https://github.com/AI-HealthCare-05/AH_05_04/pull/474#pullrequestreview-5193439578)과 남한솔의 [Frontend 인수 승인](https://github.com/AI-HealthCare-05/AH_05_04/issues/202#issuecomment-5658885279)이 기록됐다.
- Frontend 통합: #535와 #579가 develop에 병합됐고 #421 closeout 브랜치의 browser E2E가 원본 날짜·과거 약 identity·Check-in mutation 0·지연 응답 navigation 차단을 검증한다.

## 구현 및 완료 범위

- #430에서 Notification 모델·Repository·Service·라우터·migration·계약 테스트와 OpenAPI를 함께 구현했다.
- #202/#474에서 occurrence 날짜·원래 version medication 조회 및 Frontend fixture 인계를 완료했다.
- #535/#579와 #421 closeout 검증은 Notification read, historical handoff와 route-leave race를 Frontend에서 고정한다.
- 외부 Push·SMS·Email 및 Track C/Safety 자동 이동은 이 계약 범위가 아니다.

## 구현 PR 검토 범위

알림 저장·목록·읽음·재알림, 생성·게시 one-shot 명령과 처방 version 취소 port adapter를 구현한다. 상세 operation ID, 404 코드, UTC fingerprint, FK 삭제 동작, 배치 transaction과 downgrade guard는 [현재 계약](../../contracts/current/track-b-notifications-v1.md)에 기록했다. #415는 제품 결정 선행 근거이고, Current 승격의 직접 근거는 위 #430/#474 리뷰·병합과 Frontend 인수 기록이다.

#202 Schedule PUT/PATCH·Occurrence GET과 원래 약 표시 조회, Frontend Notification 소비 경로는 현재 develop에 반영되어 있다.
