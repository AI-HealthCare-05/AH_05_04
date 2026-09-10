# PD-203 — 앱 내부 알림과 재알림 상세 계약 제안

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed · 도메인 조율 대기 · 미구현 |
| 기준일 | 2026-09-10 |
| Issue | [#203](https://github.com/AI-HealthCare-05/AH_05_04/issues/203) |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 기술 리뷰 | 송은영 (`phina-io`) — Track B Backend·DB·Security |
| 소비 계약 리뷰 | 남한솔 (`solia142`) — Frontend |
| 상세 제안 | [Notification 계약 v1](../../contracts/proposed/track-b-notifications-v1.md) |

## 문제와 승인 경계

#202는 Schedule·Occurrence·Check-in API를 구현하며, #203은 Notification 저장·목록·읽음·재알림을 담당한다. 최신 develop `99bb259`에는 B1~B3 모델·서비스와 미전달 알림 취소 port가 있으나 Notification 모델·API는 없다. #203의 리뷰어 지정은 계약 승인이나 구현 PR 승인으로 취급하지 않는다.

Approved Contract Freeze v4와 원본 `FinalProject Documents/04_Decision/track-b-adherence-v1.md`는 `notification_record`의 최소 필드, 사용자 요청 재알림 1회, 멱등성, 활성 재알림 중복 오류를 정한다. 반면 `kind`·`status` 값, 읽음 필드, 목록·읽음 API, 재알림 시간 입력과 허용 상태는 고정하지 않는다. 이 상세 계약은 아래 제안에 대한 도메인 조율 전에 구현 근거로 사용할 수 없다.

## 확정 원본과 오래된 요약의 차이

처방 version 활성화의 transaction owner와 B 동기 취소 port는 원본 Freeze v4의 처방 변경 절 및 저장소 [처방 버전 목표 계약](../../contracts/targets/post-mvp-1/prescription-version-v1.md)에 이미 명시돼 있다. 같은 transaction에서 `effective_at` 이후 이전 version의 `PENDING` occurrence와 미전달 알림을 취소하며 Outbox나 사후 보상을 사용하지 않는다.

[Check-in 목표 계약](../../contracts/targets/post-mvp-1/checkin-v1.md)의 transaction 결합 미정 문구와 [테스트 전략](../../testing.md)의 후속 Decision 대기 문구는 위 원본과 불일치한다. #203 기술 리뷰에서 원본과 대조하여 이 두 요약을 동기화한다. 본 제안은 새로운 transaction 방식을 선택하지 않는다.

## 제안하는 결정

1. 기존 최소 모델 `notification_record` 하나를 사용한다. 외부 Message·Recipient·전송 큐를 신설하지 않고 SELF 소유권은 occurrence parent chain에서 확인한다.
2. 최초 알림과 재알림을 구분하고 전달 상태와 읽음 여부를 별도로 저장한다. 앱 내부 목록에 게시하는 DB 전이를 전달로 정의한다. HTTP GET은 상태를 바꾸지 않는다.
3. 최초 알림은 occurrence의 기존 예정 시각을 사용한다. 재알림은 사용자가 명시한 미래 시각을 받아 확인 기한 안에서 1회만 생성한다. 알림 읽음·취소에서 복약 결과를 만들거나 수정하지 않는다.
4. 알림 생성·게시·취소는 occurrence 잠금 뒤 Notification row를 처리한다. 처방 변경은 이미 승인된 B 취소 port에 같은 session의 구현을 주입한다.
5. 사용자 상태가 확정된 occurrence의 신규 재알림은 허용하지 않는 안을 제안한다. `NOT_TAKEN`에서 Track C `REMINDER_SETUP`이 같은 endpoint를 소비해야 하는지는 남한솔·권가빈이 확인하고, 필요하면 Track C 책임자와 허용 조건을 먼저 조율한다.

새 테이블은 알림의 게시·읽음·취소를 Check-in과 독립적으로 보존하기 위해 필요하다. occurrence에 필드를 추가하는 대안은 최초 알림과 재알림을 별도로 보존하지 못한다. 외부 전송 infrastructure는 이번 범위에 필요하지 않다. 추가 비용은 모델·migration 1개, Repository·Service·API 및 생성·게시 명령과 테스트다.

## 병렬 진행과 완료 조건

- 전용 worktree: `/private/tmp/ah-05-04-issue-203`, 브랜치: `codex/203-notifications`.
- 승인 전: 이 Decision·상세 계약·통합 접점·검증 시나리오를 준비한다. API·enum·DB migration을 선반영하지 않는다.
- 조율 후: #202 API 파일과 분리해 Notification 모델·Repository·Service·라우터·테스트를 구현한다.
- #202 병합 후: 최신 develop 반영, dependency 주입과 일정 변경 취소 연결, 실제 API 통합, OpenAPI·공통 문서 정합화를 검증한다.
- migration은 작성 전 및 PR 직전 최신 develop을 반영하고 단일 Alembic head, upgrade 및 rollback 검증을 기록한다.
- 기술·소비 계약 조율 및 구현 PR의 지정 리뷰어 승인 증빙 없이는 완료·current 승격·merge로 처리하지 않는다.
