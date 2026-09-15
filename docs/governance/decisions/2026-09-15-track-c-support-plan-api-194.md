# PD-194 — Support 1개 제안·Plan 생성

- 상태: 제품 범위 확인 / HTTP 구체화는 담당 리뷰 대기, Current 아님.
- 제품 확인·구현: 권가빈 @hazelnutflavoured (2026-09-15 작업 대화).
- 책임 리뷰어: @phina-io — Backend·Transaction·Security 및 Frontend 소비 계약.
- Frontend 소비 의견: @solia142 (#139). 별도 필수 PR 리뷰어로 지정하지 않는다.
- 근거: [#194](https://github.com/AI-HealthCare-05/AH_05_04/issues/194),
  [PD-192-2](2026-09-15-track-c-handler-config-rules-192.md).

## 제품 결정

1. eligible 후보를 `priority ASC, support_code ASC`로 정렬해 첫 1개만 제안한다.
   후보가 없으면 0개와 `NO_ELIGIBLE_SUPPORT`를 유지한다. 기존 최대 2개 목표를 대체한다.
2. 승인된 6개 안내형 설정을 사용하고 추가 입력·RAG·LLM·새 Constraint Handler는 도입하지 않는다.
3. 서버의 단일 제안과 사용자의 확정은 별개다. `confirmed=true`를 명시한 요청만 Plan을 생성한다.
4. **이번 구현은 지원 제안·Plan 생성만**이다. 기존 Plan 모델에 revision이 없음을 확인한 뒤,
   PM이 revision 추가 및 변경 API 대신 이 범위를 선택했다. #194 전체 완료로 처리하지 않는다.
5. 후속 REMINDER_SETUP 완료는 기존 일정 확인 또는 일정 저장 성공 뒤 사용자가 명시적으로 확인하는
   기준이다. 화면 진입·Plan 생성만으로 완료하지 않는다. 이번에는 완료 endpoint를 구현하지 않는다.

## HTTP 구체화 및 기술 리뷰 범위

[API 계약](../../contracts/proposed/track-c-support-plan-api-194.md)에 두 route의 DTO·오류·잠금·멱등성을 정의한다.
immutable Barrier ID와 해당 부모의 Check-in revision·최신 Safety ID·최신 Barrier ID로 생성의 현재성을
검증한다. 생성 요청에 존재하지 않는 Plan revision을 요구하거나 DB column을 추가하지 않는다.
활성 Plan unique와 Check-in 선행 잠금으로 동시 생성의 중복을 차단한다.

새 오류·확인 필드·응답 shape는 이 Decision의 구체화 delta이며 Freeze v4에 이미 있던 것으로 쓰지 않는다.
전체 target의 Current 승격, 임상 Safety 정책 구현, Production 공개 승인과 구분한다.

## Revision 2 — PR #608 리뷰 반영

Jye-rookie의 a37ef794 대상 리뷰 1·2번을 PM 요청으로 반영한다. 책임 리뷰어는 @phina-io로 유지한다.
GET은 FOR UPDATE 없이 단일 SQL statement snapshot으로 조회한다. 조회 중 Check-in 정정을
막지 않으며 POST에서만 기존 잠금 순서와 현재성 재검증을 수행한다.
설정 쌍은 신규 POST의 행 잠금 전에 각 파일을 한 번씩 읽어 검증하며 파일 I/O를 스레드에 위임한다.
새 캐시/설정 활성화 기능은 추가하지 않는다. 설정 장애와 stale이 겹치면 신규 POST의 설정 503이
먼저 반환될 수 있다. 기존 성공 멱등 replay는 설정을 로딩하지 않는다.
DTO·개수·소유권·일반 Safety 허용 조건과 공개 gate는 바꾸지 않는다.
