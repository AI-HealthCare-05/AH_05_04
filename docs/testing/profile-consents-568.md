# Profile 목적별 동의 관리 검증 (#568)

## 범위와 기준

- 기준: develop `3e0c4f4b`, [현재 사용자 동의 계약](../contracts/current/user-account.md#목적별-동의-상태-api207).
- 구현 담당: @hazelnutflavoured. 책임 리뷰어: 남한솔 (@solia142).
- Profile에서 OCR / GUIDE / CHAT / NOTIFICATION 조회·철회만 구현한다. 회원가입 #562와 별도 PR이며 재동의, Backend Gate, #549 실제 가입 검증은 범위 밖이다.
- `GET /api/v1/users/me/consents`와 `PUT /api/v1/users/me/consents/{purpose}`를 기존 계약대로 소비한다. 공유 API·DTO·DB·오류 의미를 변경하지 않는다.
- `is_granted`로 유효 동의를 표시하고 `status=null`, `WITHDRAWN`, 유효하지 않은 `GRANTED`를 구분한다.
- 철회는 서버 `current_policy_version`을 사용한다. OCR 현재 정책이 비어 있으면 저장된 `policy_version`으로 철회한다. 버전 조회 실패 시 임의 버전을 만들지 않고 재조회를 제공한다.
- 철회 응답을 받지 못한 목적은 확인 불가로 표시하고 재조회한다. 다른 목적 상태는 유지한다. 정책 불일치는 재조회 후 사용자가 다시 철회한다.
- 철회가 기존 데이터 삭제·회원탈퇴와 다름을 안내한다. Guide/Chat/Notification 실행 Gate 완료나 외부 Privacy·Production 승인을 의미하지 않는다.

## 자동 검증 (2026-09-15)

잠금 파일 기준 `pnpm install --frozen-lockfile` 후 실행했다. 로컬 pnpm의 자동 의존성 재검사 오류로 test/lint/build는 동일 package script의 실행 파일을 직접 호출했다.

- `VITE_API_BASE_URL=http://localhost:8000 ./node_modules/.bin/vitest run`: 30 files, 551 tests PASS.
- `./node_modules/.bin/oxlint`: PASS.
- `./node_modules/.bin/tsc -b && ./node_modules/.bin/vite build`: PASS.
- `./node_modules/.bin/playwright test`: 최초 10 PASS / 1 FAIL. 기존 계정 fixture의 새 목록 GET 누락을 보완한 뒤 `playwright test auth-and-account.spec.ts` 2 PASS. 나머지 9개는 최초 실행에서 PASS.
- `playwright test --config playwright.email-verification.config.ts`: 1 PASS.
- Profile 단위 테스트: 네 목적별 철회와 다른 목적 유지, missing/withdrawn/invalid 구분, 서버 버전, OCR 빈 현재 정책, 조회 실패, PUT 응답 유실·422·503, 재조회, 중복 클릭, GET/PUT 401.
- Profile 브라우저 테스트: 320/390/412px, 실제 공통 API client의 GET/PUT 경로·본문, 조회 시 mutation 0, 키보드 철회, 재접근 후 서버값, 수평 overflow·버튼 경계 확인.
- Contract/Service: 최초 374 PASS, uv 캐시 권한으로 2 FAIL, Docker 소켓 권한으로 7 ERROR. uv 접근을 허용한 관련 파일 재실행은 38 PASS. Docker 접근을 허용한 관련 파일 재실행은 10 PASS로 최초 오류 7개를 모두 해소했다. 최종 미해결 실패 없음.
- `git diff --check`: PASS.

## 화면 및 한계

320px 스크린샷에서 카드 제목·상태·철회 버튼과 안내의 줄바꿈을 직접 확인했다. 세 viewport 브라우저 검증의 이미지 경로는 `frontend/test-results/profile-consents-{width}.png`이며 생성 파일은 커밋하지 않는다.

모든 브라우저 응답은 합성 fixture다. 실제 Backend·DB·외부 Provider 연결 및 #549 SMTP/가입 gate 검증은 수행하지 않았다. Python/DB 구현 변경은 없다. PR의 필수 CI 및 책임 리뷰어 승인은 별도로 확인해야 한다.
