# #421 Notification Frontend real-stack 통합 증빙

`frontend/e2e-real-stack/notification-round-trip.spec.ts`는 격리된
`docker-compose.real-stack-e2e.yml`의 PostgreSQL, FastAPI, Frontend를 함께 사용한다.
브라우저 route interception, FastAPI dependency override, fake API는 사용하지 않는다.

합성 seed는 `backend/app/release_validation/notification_round_trip_fixture.py`에서만 준비한다.
`ENV=local`, `DB_NAME=dosey_e2e`가 아니면 즉시 중단하며, 사용자·처방 version·일정·occurrence·알림은
각각 `UserRepository`, `PrescriptionRepository`, `MedicationScheduleRepository`,
`NotificationRepository`와 `NotificationScheduler` 경계를 사용한다. 실제 환자 자료는 사용하지 않는다.

## 검증 역할

- real-stack E2E: 실제 `GET /api/v1/notifications`, unread 알림의 실제 read `PATCH` 1회와 DB 저장,
  already-read 재선택 시 추가 `PATCH` 0회, 서버 `occurrence_local_date` 기반 route, 실제 occurrence day와
  `GET /api/v1/medication-occurrences/{occurrence_id}/medication`, 과거 처방 version/medication identity,
  current medication fallback 0회, Check-in mutation 0회를 담당한다.
- fixture browser E2E (`frontend/e2e/notification-closeout.spec.ts`): 320/390/412px 반응형·키보드·focus,
  identity 불일치 중립 오류와 #551 지연 응답 navigation 차단을 결정적으로 담당한다.

두 E2E는 서로 대체 관계가 아니다. real-stack은 #203 Backend API와 #138 occurrence 화면의 실제 연결을,
fixture E2E는 네트워크 timing과 오류를 제어해야 하는 UI 회귀를 증명한다.

재현 명령:

```bash
bash scripts/e2e/real_stack.sh test-notification
```

이 runner는 테스트 종료 시 격리 stack과 임시 volume을 정리한다. `KEEP_REAL_STACK=1`은 로컬 진단 시에만
명시적으로 사용한다.

## 2026-09-15 실행 결과

- real-stack browser E2E: **1 passed**.
- 실제 Notification GET에서 합성 unread 1건을 확인했다.
- unread 선택의 실제 read PATCH는 1회였고, 후속 Notification GET에서 `read_at` 저장을 확인했다.
- 저장된 read 알림 재선택의 추가 PATCH는 0회였다.
- occurrence day와 medication 상세의 세 identity가 일치하고 현재 처방 version/medication과 달랐다.
- UI handoff 중 current prescription 조회와 Check-in mutation은 각각 0회였다.
- URL의 `date`는 Notification 응답의 `occurrence_local_date`와 동일했다.
