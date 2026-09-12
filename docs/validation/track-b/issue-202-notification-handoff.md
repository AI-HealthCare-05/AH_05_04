# 알림→복약 기록 Backend 통합 및 Frontend 인계 (#202/#421)

| 항목 | 값 |
| --- | --- |
| 상태 | 기존 구현 관찰·검증. 새 계약 또는 Frontend 완료 증빙 아님 |
| 기준일 | 2026-09-12 |
| 기준 코드 | develop `df259ab3` (#430 알림·#456 일정 API 포함) |
| 구현·인계 자료 담당 | 권가빈 (`hazelnutflavoured`) |
| Backend/API·Security 리뷰 | 송은영 (`phina-io`), #202 지정 |
| Frontend 소비 계약 리뷰·구현 | 남한솔 (`solia142`), #202/#421 지정 |
| Migration head | `e8c41a09d652`, 변경 없음 |
| 범위 | Backend HTTP 통합 테스트·합성 응답 fixture·인수 시나리오. runtime 코드/계약 변경 없음 |

## 확인한 동작과 남은 연결

알림의 원래 날짜로 occurrence를 찾고, 읽음과 명시적 Check-in을 구분하는 경로는 실제 API로
검증했다. 처방 정정 후에도 전달된 알림·과거 occurrence·Check-in은 원래 식별자를 유지한다.
그러나 **과거 occurrence의 약 표시 정보를 현재 처방 상세 응답으로 연결하는 것은 불가능한
사례가 확인됐다.** 이 결과를 전체 알림→과거 약 표시 경로 완료로 해석하지 않는다.

| 경로 | 관찰 결과 | 인계 기준 |
| --- | --- | --- |
| `GET /api/v1/notifications` | DELIVERED 목록에 occurrence_id·occurrence_local_date 제공 | 이 두 값을 원래 기록 연결에 사용 |
| `GET /api/v1/medication-occurrences?date=...` | 해당 날짜의 occurrence·version 약 ID·Check-in 제공 | notification scheduled_at에서 날짜를 재계산하지 않음 |
| `GET /api/v1/prescriptions/latest` | 현재 활성 version과 medications 제공 | 정확한 version 약 ID가 일치할 때만 현재 약 표시 연결 |
| `GET /api/v1/prescriptions/{prescription_id}` | 해당 처방의 현재 활성 version 제공 | 처방 ID를 이미 알아도 과거 version 약 정보가 반환되는 것은 아님 |
| 읽음 PATCH | read_at만 변경, Check-in 0건 유지 | 읽음/클릭/화면 진입을 TAKEN으로 저장하지 않음 |
| Check-in PUT | 사용자 요청으로만 TAKEN 생성, 날짜 조회에 같은 결과 반영 | expected_revision과 Idempotency-Key를 기존 계약대로 사용 |
| 처방 정정 PATCH 후 재조회 | 새 약 ID와 과거 occurrence의 약 ID가 서로 다름 | 최신/유사 약으로 대체하지 않음 |

확인 근거:

- [Occurrence DTO](../../../backend/app/dtos/medication_schedules.py): `MedicationOccurrenceData`에는
  prescription_version_id·prescription_version_medication_id가 있고 약명·용량 표시 필드는 없다.
- [처방 서비스](../../../backend/app/services/prescriptions.py): 상세와 latest 모두
  `_active_version_data`를 사용한다.
- [실제 v1 등록](../../../backend/app/apis/v1/__init__.py), [처방 라우터](../../../backend/app/apis/v1/prescription_routers.py):
  과거 version을 지정하는 범용 약 상세 GET은 등록돼 있지 않다.
- Candidate 응답의 공식 제품명은 원래 처방의 약명·용량 snapshot을 대신하지 않는다.
- #462의 backlog는 아직 등록 변경 리뷰 대기이고 UNCONFIRMED만 대상으로 한다.
  병합돼도 TAKEN/NOT_TAKEN 등의 모든 과거 알림을 위한 범용 약 상세 조회로 사용할 수 없다.
- 기존 [알림 Proposed 문서의 표시 정보 소비 경로](../../contracts/proposed/track-b-notifications-v1.md)에
  같은 후속 확인 항목이 남아 있다. 이 PR에서 상태를 Current로 이동하거나 새 필드를 추가하지 않는다.

## 실제 HTTP 시나리오

[테스트](../../../backend/app/tests/notifications/test_notification_record_handoff.py)는 기존 notification
합성 fixture와 실제 ASGI 앱·라우터·Service·Repository·PostgreSQL을 사용한다. 인증 user와
알림 service clock만 기존 fixture 방식으로 대체한다. 외부 Provider는 호출하지 않는다.

1. 현재 처방 조회, 최초 알림 생성·게시, KST 다음 날 01:00 재알림 게시.
2. 재알림의 occurrence_local_date는 원래 날짜인 2026-09-10임을 확인하고 날짜 GET으로 연결.
3. 현재 version 약 ID가 현재 처방 medications의 ID와 일치함을 확인.
4. 읽음 snapshot 실패를 주입해 503 및 read_at rollback 확인, 같은 키로 재시도·replay 성공.
5. 읽음 전후 occurrence 응답이 동일하고 Check-in·Audit 모두 0건임을 확인.
6. 사용자 TAKEN PUT 뒤 날짜 조회의 Check-in 일치 확인. stale revision의 새 요청은 409이며
   재조회한 결과가 유지된다. 추가 읽음도 Check-in/Audit을 생성하지 않는다.
7. 실제 처방 정정 PATCH로 새 version과 약 ID 생성 후 과거 날짜·알림을 재조회.
   과거 version·약 ID·Check-in 및 전달 알림 ID가 유지되고 현재 상세 응답의 약 ID와 다름을 확인.
8. 다른 사용자로 알림 읽음 및 Check-in 직접 링크 요청 시 404, 목록·날짜 조회는 빈 목록,
   새 Check-in은 0건임을 확인.

정상·오류 응답의 no-store와 기존 오류 code도 확인한다. 네트워크 단절, 모바일 렌더링,
실제 사용자 로그인 및 브라우저 E2E는 이번 Backend 검증에 포함하지 않는다.

## 한솔님용 합성 fixture

[issue-202-notification-handoff.json](./issue-202-notification-handoff.json)은 실제 테스트의 응답을
추출한 `SYNTHETIC` 데이터다. API 요청·응답 계약을 새로 정의하는 파일이 아니다.
테스트가 Pydantic DTO 및 응답 간 식별자·revision 관계를 검증한다.

| fixture key | 쓰임 |
| --- | --- |
| current_prescription | 연결 가능한 현재 처방·약 ID |
| notifications | 자정 경계를 넘는 재알림과 최초 알림 |
| day_before_read / day_after_read | 읽음 전후 Check-in 불변 |
| read_failure / read_response | snapshot 503 후 재시도 성공; 일반 5xx 전체를 이 code로 취급하지 않음 |
| checkin_request / checkin_response / checkin_conflict | 명시적 TAKEN·저장 결과·stale revision 409 |
| current_prescription_after_correction | 변경된 현재 version·약 ID |
| historical_day / notifications_after_correction | 원래 version의 기록과 전달 이력 보존 |

Fixture의 UUID·document_id는 모두 테스트에서 생성한 합성 식별자다. 현재 응답에 없는 약명
필드를 occurrence에 추가하거나 누락된 과거 약명을 현재 medications에서 채우지 않는다.
`historical_day.schedule_items`는 현재 version의 설정 상태이고, `occurrences`는 요청 날짜의
과거 기록일 수 있다. 배열 순서나 display_order가 같다는 이유로 연결하지 않는다.

## 오류·재접속 인수 시나리오

| 사례 | 소비 기준 | 증빙 상태 |
| --- | --- | --- |
| 읽음 503→재시도 | 성공 응답 전 읽음 성공으로 확정하지 않음; 동일 요청 재시도 | 실제 HTTP 테스트·fixture |
| 읽음 replay | 같은 결과를 반영하고 복약 상태를 변경하지 않음 | 실제 HTTP 테스트 |
| Check-in revision 409 | 최신 날짜별 기록 재조회 후 사용자 의도로 재시도 | 실제 HTTP 테스트·fixture |
| 권한/타인 링크 | 읽음·Check-in 404, 목록/날짜는 빈 목록. 존재 여부를 추가 노출하지 않음 | 실제 HTTP 테스트 |
| 인증 401 | 기존 인증 복구 흐름 사용, 자동 Check-in 금지 | 기존 알림 인증 테스트; 화면 검증 남음 |
| 잘못된 요청 422 | code와 입력값 기준 처리; 새 enum/필드 생성 금지 | 기존 알림/일정/Check-in 테스트 |
| 새로고침·재접속 | 알림 offset=0 재조회 후 같은 occurrence ID 연결. 읽음 여부와 Check-in을 각각 최신화 | Frontend 실제 소비/E2E 남음 |
| 과거 약 ID가 현재 상세에 없음 | 최신/유사 약으로 대체하지 않음. occurrence 자체는 조회됐으므로 약 표시 실패 UX는 제품·Frontend와 별도로 합의 | 구체 사례 재현; 과거 약 표시 조회 계약 합의 남음 |
| 모바일·접근성 | 320/390/412px, 긴 약명, 키보드·focus·상태/오류 알림, 읽음 전후 복약 상태 불변 | #421/#138 구현·브라우저 증빙 남음 |

기존 자료는 [일정 fixture](./issue-202-schedule-fixtures.json), [일정 API 검증](./issue-202-schedule-api.md),
[Check-in 검증](./issue-202-checkin-api.md), [알림 검증](./issue-203-notifications.md)을 함께 사용한다.
#138의 GitHub 책임 리뷰어는 이슈상 별도 확정 필요 상태이므로 이 인계에서 임의 배정하지 않는다.

## 리뷰에서 합의할 사항

송은영·남한솔에게 다음 연결 문제의 경로를 확인 요청한다. 제안 단계의 검토 항목이며
새 API를 등록하거나 기존 DTO를 확장한 것이 아니다.

- 과거 version의 약 표시 정보를 기존 승인 경로로 조회할 수 있는지 확인한다.
- 추가 조회가 필요하면 과거 version 상세 조회와 날짜별 표시 snapshot 제공 중 적절한 범위,
  SELF ownership·반환 최소 필드·404 의미를 별도 Decision/계약으로 합의한다.
- 현재 처방 ID를 잃은 재접속 및 완전히 다른 새 처방으로 바뀐 경우도 같은 경로로 해결되는지
  확인한다. latest 한 번 조회로 모든 과거 약을 해석할 수 있다고 가정하지 않는다.

이 합의가 필요한 API/DTO 변경은 이 테스트·인계 PR의 범위 밖이다. #421 완료와 Track B
전체 인수를 표시하지 않는다. Production은 #230 및 기존 외부/Privacy 게이트 이후다.

## 검증 기록

- 신규 실제 연결/타인 접근/fixture 검증: **3 passed (1.76초)**.
- Ruff check/format PASS (776 files), Mypy PASS (588 source files).
- 전체 필수 runner PASS: **5,209 passed / 76 skipped**, 총 coverage **93%**.
  Migration 210/3, Backend 1,932/65, Redis 24/0, Worker 3,043/8 (passed/skipped).
- 구현 검증 commit: `6d4688af`. 리뷰 자료: [PR #468](https://github.com/AI-HealthCare-05/AH_05_04/pull/468).
  원격 검사는 [GitHub Actions](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/34690042782)에서 확인한다.
  문서 후속 커밋의 최종 HEAD 검사와 지정 리뷰어 승인은 PR에서 별도로 확인해야 한다.
- 재현: 기존 `scripts/ci/run_test.sh`를 합성 전용 ENV_FILE·COMPOSE_FILE로 실행한다.
  새 테스트는 기본 Backend 수집 범위에 포함된다. 전체/통합 runner를 동시 실행하지 않는다.
- Fixture 재추출은 위 테스트 실행 환경에서 `TRACK_B_HANDOFF_FIXTURE_OUTPUT`을 명시한 경우만
  수행한다. 기본 CI에서는 커밋된 fixture를 읽어 검증하며 파일을 변경하지 않는다.
- #467 정기 실행 PR의 원격 CI는 [7/7 PASS](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/34689331614).
  이 브랜치는 #467 구현을 포함하지 않는다. #462는 두 리뷰어 승인 대기 상태로 분리했다.
