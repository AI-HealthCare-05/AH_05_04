# #139 완료 계획 후기 API 연결

- 구현 담당: 권가빈 (@hazelnutflavoured).
- 단일 책임 리뷰어: 남한솔 (@solia142), #139에 기록된 Frontend 책임 리뷰 배정 유지.
- 범위: 완료 계획 후기 화면, 기존 Follow-up API 소비·오류 복구·멱등 재시도·접근성.
- 기반: #631·#629·#639가 병합된 develop `a437a7d4`.
- 계약: [Follow-up API v1](../contracts/current/track-c-followup-api-194.md).

## 사용자 동작

개발 전용 계획 화면에서 COMPLETED 계획에만 ‘도움 사용 후기’를 표시한다.
열 때 Plan GET으로 상태를 재확인한 뒤 Follow-up GET을 읽는다.
미응답은 선택되지 않은 세 radio(도움이 됐어요/도움이 되지 않았어요/아직 모르겠어요)를 제공한다.
선택 후 저장 버튼으로만 POST하며, 나중에·진입·조회는 저장하지 않는다.
저장된 응답은 별도 수정 버튼을 눌러 다시 선택하고 저장할 수 있다.
평가는 계획에 대한 의견이며 약의 효과나 복약 여부를 판정하지 않는다.

기존 디자인 프로토타입의 질문·선택·명시 저장 구조와 공통 Card/Button/radio 스타일을 사용한다.
프로토타입의 ‘조금 바꾸면 좋겠어요/다른 방법이 필요해요’를 API enum에 임의 대응하지 않고,
기존 HELPED/NOT_HELPED/NOT_SURE 의미를 그대로 표시한다. 새로운 Figma 원본 대조나 1:1 일치는 주장하지 않는다.

## 계약 소비와 실패 복구

- GET의 null은 최초 expected_revision=0, 기존 값은 해당 revision을 사용한다.
- 응답 유실은 메모리에 보관한 동일 body/key로 재시도한다. 불확실한 요청 동안 선택을 바꾸지 않는다.
- 성공 POST 응답은 과거 replay일 수 있으므로 현재 GET 결과를 표시한다. POST 성공 뒤 GET만 실패하면 조회만 재시도한다.
- revision 충돌은 최신 Plan/Follow-up을 조회하고 선택을 해제한다. 새 명시 선택 전 재제출하지 않는다.
- 401은 인증을 비우고 로그인으로 이동한다. 403/404/기타 409는 제출을 차단하며 서버 원문을 노출하지 않는다.
- ACTIVE/CANCELLED 또는 다른 Plan ID의 자료로 평가하지 않는다. 안내 Copy 503은 완료 계획 후기의 의존성이 아니다.
- 같은 화면의 중복 클릭은 단일 요청으로 제한한다. 후기·멱등 키를 브라우저 저장소에 저장하지 않는다.
- 서버 API·DTO·DB·상태·오류 의미는 변경하지 않는다. 일정·Check-in·Safety·Barrier·Plan mutation이나 자동 재추천을 추가하지 않는다.
- DEV-only 진입과 Production 제외 경계를 유지한다. 실제 사용자 공개·의료·Privacy 승인은 별도다.

## 검증

- Frontend 전체: **752 passed** (44 files).
- 집중 검사: 후기 컴포넌트 16개, Plan 화면의 완료 상태·자료 실패 연결 3개 추가, API body/key/envelope 검사 1개 추가.
- TypeScript, oxlint, production build, `git diff --check` 통과. 기존 500 kB 번들 경고는 유지된다.
- Playwright: **8 passed**. 새 후기 시나리오 320/390/412px와 기존 Track C 5개.
  진입·나중에 무저장·저장·새로고침·정정, radio/버튼과 질문 focus, 가로 overflow를 검증했다.
  320px 스크린샷을 직접 확인했다. 생성물은 커밋하지 않는다.
- 실제 FastAPI + 격리 PostgreSQL + Chromium: **1 passed**. API interception 없이 계획 생성·완료·
  HELPED 저장·새로고침·NOT_HELPED 정정·GET revision=2와 원래 plan ID를 확인한다.
  기존 일정/외출/잊음 세 경로와 취소 흐름도 유지한다. 합성 fixture만 사용했다.
- Backend 구현은 변경하지 않아 전체 Python 회귀를 로컬에서 다시 실행하지 않았다.

## 재현

Frontend 설정에서 VITE_API_BASE_URL을 지정한 뒤 `pnpm test`, `pnpm lint`, `pnpm build`.
브라우저는 `pnpm exec playwright test track-c-followup.spec.ts track-c-integration.spec.ts`.
실제 API 검증은 격리된 dosey_e2e DB에 report_round_trip_fixture를 준비하고
TRACK_C_E2E_SYNTHETIC=1, TRACK_C_E2E_API_URL, REAL_STACK_WEB_URL을 설정해
`pnpm exec playwright test --config=playwright.real-stack.config.ts track-c-round-trip.spec.ts`를 실행한다.

#139 전체 완료는 아니다. 승인된 증상별 Safety·약별 근거/Citation·일반 흐름 재진입 복원은 별도다.
