# 회원가입/Auth Frontend 인수인계 검증 — #562

## 기준과 범위

- 확인일: 2026-09-15
- 기준: PR #562 (`feat/532-signup-consents`, `dc37b8e8`)의 후속 로컬 변경
- 화면: [AUTH-01](https://www.figma.com/design/hT9J1Rq8R1ynS4aCa4Zk55/?node-id=1157-148), [AUTH-LEGAL-01](https://www.figma.com/design/hT9J1Rq8R1ynS4aCa4Zk55/?node-id=2102-74)
- API 정본: [현재 계정 계약](../contracts/current/user-account.md), [#532](https://github.com/AI-HealthCare-05/AH_05_04/issues/532)
- 공유 API·DTO 변경 없음. 필수 약관 체크는 화면의 제출 조건이며 서버 저장 필드를 새로 만들지 않는다.
- SMTP/Secret, Backend 구현, 최종 법무 문안 확정, Production gate 활성화는 제외한다.

## 구현과 디자인 적용

- 필수 약관 체크와 기능별 선택 동의를 분리한다. 필수 약관 미동의 시 CTA 비활성화와 submit handler 검증을 함께 적용한다.
- 네 목적은 기본 미선택이며 선택 0개도 가입 가능하다. 선택한 목적만 `{ purpose }`로 전송한다. `policy_version`, `status`, 필수 약관 체크 값은 signup payload에 추가하지 않는다.
- `/signup?terms=review`는 검토용 약관 화면이다. 열기·확인·화면 뒤로·브라우저 뒤로는 동의 mutation을 호출하지 않는다. 같은 화면 내 왕복은 입력값·선택·인증 상태를 유지한다. 새로고침 이후 유지하도록 개인정보를 저장하지 않는다.
- `409 EMAIL_VERIFICATION_REQUIRED` 수신 시 인증 완료 표시와 코드를 초기화하고 재인증을 안내한다. 이메일 중복 오류와 구분한다. UI flag가 꺼진 경우 이용 불가 안내를 표시하며 인증 API를 자동 호출하거나 flag를 켜지 않는다.
- 약관의 검토용/승인 전 표시를 유지한다. Figma의 `policy_version`/`status` 구현 설명은 사용자 본문에서 제외한다.
- Figma의 네 목적 모두 Profile에서 철회 가능하다는 문장은 현재 코드와 다르므로 OCR만 제공 중이며 나머지는 준비 중으로 정렬한다.
- Figma의 이메일 `중복확인` 버튼은 인수인계의 명시적 API 기준에 따라 구현하지 않는다. 기존 #545 인증 UI와 signup의 이메일 중복 처리를 유지한다.

## 검증

- `VITE_API_BASE_URL=http://localhost:8000 pnpm exec vitest run tests/SignupPage.test.tsx tests/AuthApi.test.ts`: 29 passed
- `VITE_API_BASE_URL=http://localhost:8000 pnpm test`: 30 files, 547 passed
- `pnpm lint`, `pnpm build`: 통과. build는 번들 500 kB 초과 경고를 출력한다.
- `pnpm run test:e2e:requirements`: Chromium 기본 9개 + 인증 UI 1개 통과
- 390px 브라우저에서 필수 약관 비활성 CTA, 검토용 표시, 약관 왕복 후 focus·입력 유지, mutation 0, 선택 0개 가입, 기존 인증 재요청·중복 오류를 확인한다.
- 인증 만료 후 재인증과 동의 선택 보존은 unit mock 응답으로 검증한다. #549 실제 Backend gate·SMTP 연결 검증은 아니다.
- 초기 unit 실행의 API URL 누락 및 브라우저 서버의 sandbox port 권한 실패는 각각 테스트용 URL 설정과 로컬 실행 권한으로 해소했다.
- Backend/Python/DB/외부 Provider 테스트는 Frontend 전용 변경이므로 실행하지 않았다.

## 후속 및 종료 audit

- Profile 기준 코드: `frontend/src/pages/ProfilePage.tsx`, `frontend/src/api/ocrConsent.ts`. 현재 OCR 전용 GET/DELETE만 사용한다. GUIDE/CHAT/NOTIFICATION 목록 조회·철회 UI는 별도 Frontend 후속으로 분리한다. 해당 후속은 현재 목적별 GET/PUT 계약을 소비하고 서버 version을 사용해야 한다.
- #532: 이번 변경의 책임 리뷰·#562 병합 후 종료 가능 여부를 판단한다. 아직 종료하지 않는다.
- #431: #562만으로 종료하지 않는다. #549는 확인 시점에 OPEN/Draft/CHANGES_REQUESTED이며 아직 병합되지 않았다. 이메일 인증 UI와 Backend gate·운영 Provider의 통합 준비 조건이 남아 있다.
- #562: 확인 시점 OPEN/REVIEW_REQUIRED. 기존 PR 구현 담당자는 @phina-io, 책임 리뷰어는 @solia142이다. 인수인계 이후 담당자 표기와 약관 검토 범위는 PR 갱신 시 확인할 항목이다. 이 기록은 리뷰 승인이나 법무/Privacy 승인을 대신하지 않는다.
