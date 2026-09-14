# #507 회원가입 이메일 인증 Frontend 검증

- 구현 담당자: 권가빈 (`hazelnutflavoured`)
- 담당 리뷰어: 남한솔 (`solia142`)
- 범위: Signup UI와 기존 이메일 인증 API 소비, Frontend 단위·브라우저 회귀
- 기준: `f97c280e` develop, 2026-09-14 로컬 검증

## 계약 소비

[현재 사용자 계정 계약](../contracts/current/user-account.md)의 request/confirm API를 소비한다.
공유 API·DTO·오류 코드·Backend signup 계약은 변경하지 않는다. Feature flag가 켜진 경우 인증 완료 전 가입 제출을
막는 것은 #507의 Frontend 흐름이며 Backend 인증 강제 조건을 추가한 것으로 해석하지 않는다.

요청 성공은 이메일 존재 여부·cooldown·LOCAL token 유무와 무관하게 같은 안내를 사용한다.
request adapter는 응답을 반환하지 않아 LOCAL token을 UI에서 사용할 수 없다. 코드는 사용자
입력으로 받아 컴포넌트 메모리에서만 유지하고 확인 성공·이메일 변경 시 지운다. token을 로그,
URL 또는 Web Storage에 쓰지 않는다. 실패 원문 대신 고정된 중립 안내를 사용하며 token의
invalid/expired/reused/이메일 불일치는 하나의 실패 UX로 처리한다. 최종 signup의 409 CONFLICT는
이메일 필드 오류로 별도 표시한다. 요청·확인 중 이메일을 read-only로 두며 중복 요청·제출을 막는다.

## 자동 검사

| 검사 | 결과 |
| --- | --- |
| `VITE_API_BASE_URL=http://localhost:8000 pnpm --dir frontend test` | 24 files, 435 tests 통과 |
| `pnpm --dir frontend lint` | 통과 |
| `pnpm --dir frontend build` | 통과; bundle 크기 warning 존재 |
| `pnpm --dir frontend run test:e2e:requirements` | 기본 OFF Chromium 8 tests + 명시적 ON 1 test 통과 |
| `ruff check .`, `ruff format . --check` | 통과 (880 files) |
| `mypy backend/app ai_worker` | 기존 로컬 Python 환경의 pgvector, py_vapid, pywebpush, http_ece 누락으로 실행 실패 |
| `bash scripts/ci/run_test.sh` | 기존 로컬 Python 환경의 pgvector 누락으로 migration 사전 검사에서 중단; 전체 Backend 테스트 미완료 |
| `git diff --check` | 통과 |

Frontend 첫 실행은 VITE_API_BASE_URL 누락으로 기존 API 테스트가 실패했고 위 합성 localhost
설정을 지정한 재실행에서 모두 통과했다. 기존 가입 E2E의 이메일 locator는 새 코드 label과
부분 일치해 실패했으나 exact selector로 수정한 후 통과했다.

브라우저 테스트는 390×844에서 가로 넘침·입력과 버튼 폭을 검사하고, 이메일에서 Tab으로
인증 요청 이동, 요청 후 코드 focus, Enter 확인, 실패 후 코드 focus, 성공 후 비밀번호 focus,
409 후 이메일 focus를 검증한다. 실패와 완료 화면 PNG를 직접 확인했다. 이미지 산출물은
`frontend/test-results/requirements/signup-507-mobile-{failure,verified}.png`에 로컬 저장한다.

## 최초 구현의 실제 로컬 Backend 통합 smoke (a5a2e747)

기존 LOCAL Backend `http://127.0.0.1:18471`에 새 worktree Frontend의 실제 adapter 요청을
Playwright route proxy로 전달했다. 응답을 mock하지 않았으며 새 합성 `@example.com` 계정을 사용했다.

- 인증 요청: 200
- 인증 확인: 200
- 최종 signup: 201
- 로그인 화면 이동: 통과

LOCAL 전용 응답 token은 테스트 harness 메모리에서만 읽어 이메일 수신을 대신해 코드 입력란에
입력했다. token·비밀번호·응답 본문은 로그, trace, screenshot 또는 파일에 저장하지 않았다.
이 smoke는 실제 이메일 발송·수신이나 Production/Staging Provider 검증을 의미하지 않는다.
#494 Provider 활성화와 공개 게이트는 이 변경 범위에 포함하지 않는다.

## 리뷰 반영: Provider 활성화 전 기존 가입 유지

[리뷰 blocker](https://github.com/AI-HealthCare-05/AH_05_04/pull/545#pullrequestreview-5197380137)를
수용해 `VITE_EMAIL_VERIFICATION_ENABLED`를 추가했다. 미설정·`false` 및 `true` 이외 값은
모두 OFF다. 기본 local/noop·Staging·Production에서 인증 UI를 숨기고 기존 validation과 signup,
409 focus 처리를 유지하며 request/confirm을 호출하지 않는다. API 성공 여부로 Provider 상태를
추측하거나 인증 실패 시 자동 우회하지 않는다.

명시적인 `VITE_EMAIL_VERIFICATION_ENABLED=true` 빌드에서만 인증 UI와 필수 인증을 함께 켠다.
Vite build-time 설정이므로 변경 후 재빌드가 필요하다. #494의 해당 환경 메일 발송 경로가 준비되고
실제 전달을 확인한 뒤 활성화한다. 이 PR은 배포 환경에 true를 설정하지 않으며 기존 공개 게이트도
변경하지 않는다. LOCAL 테스트도 실제 수신 가능한 발송 경로를 준비한 경우에만 켠다.

- `frontend/.env.example`: OFF 기본값과 활성화 전제 명시
- `playwright.config.ts`: OFF 기존 가입 회귀
- `playwright.email-verification.config.ts`: 격리된 포트에서 ON 인증 회귀
- `test:e2e:requirements`: 두 설정을 순서대로 실행하므로 기존 CI 명령도 두 경우를 검증
- 최신 검증: 단위 435개, E2E OFF 8개 + ON 1개, lint·build·diff check 통과
- flag 미설정 production 빌드를 `vite preview`와 Chromium 390px에서 검증: 인증 UI 없음,
  signup만 1회 호출, 로그인 화면 이동. 이 회귀는 합성 API 응답을 사용하며 실제 Provider 검증이 아니다.
- Python 공통 검사 제한은 최초 검증과 동일하며 이번 Frontend 수정에서 재실행하지 않았다.
