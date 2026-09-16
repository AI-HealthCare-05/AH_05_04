# Production 회원가입 복구

기준: `dac51c29`. 2026-09-16 요청자가 해당 커밋의
`frontend/src/pages/TermsOfService.tsx` 내용을 그대로 승인 완료했다고 본 작업 대화에서 확인했다.
외부 승인 기록 링크는 제공되지 않았다. 이 확인은 회원가입 약관에만 해당하며
Track C/F 또는 공통 Privacy Production gate 공개 승인을 의미하지 않는다.

중복확인 버튼은 미구현 상태로 disabled가 고정되어 있었다. 가입 시 기존 API의
이메일 중복 검증을 사용하며 별도 중복확인 API를 추가하지 않는다.
약관 승인 flag가 빌드에 전달되지 않아 필수 동의와 가입 버튼도 비활성화되어 있었다.

## 배포

운영 환경파일에서 `VITE_SIGNUP_TERMS_APPROVED=true`로 설정하고 새 고정 이미지 버전으로
`scripts/deployment.sh`를 실행한다. 기존 파일에 false가 명시돼 있으면 true로 변경해야 한다.
현재 승인된 문서에 대해 production Docker build와 배포 스크립트의 기본값은 true다.
새 미승인 약관을 배포할 때는 명시적으로 false를 지정한다.

`VITE_EMAIL_VERIFICATION_ENABLED`는 실제 이메일 발송 준비 상태 및 Backend의
`SIGNUP_EMAIL_VERIFICATION_REQUIRED`와 맞춘다. 이 변경은 이메일 인증을 자동 활성화하지 않는다.
Vite 값은 빌드 시 정적 번들에 포함되므로 서버 환경변수 수정이나 컨테이너 재시작만으로는 적용되지 않는다.
Frontend 이미지를 재빌드하고 해당 이미지로 nginx 서비스를 갱신해야 한다.

## 확인

- 약관 보기에서 승인 전 초안 표시가 없어야 한다.
- 필수 동의를 체크하면 가입 버튼이 활성화되어야 한다.
- 중복확인 버튼 대신 가입 시 이메일 중복을 확인한다는 안내가 표시되어야 한다.
- 선택 동의 없이도 가입할 수 있어야 한다. 선택 동의 시 별도로 각 목적의 정책 버전 설정이 필요하다.
- `VITE_SIGNUP_TERMS_APPROVED=false` 빌드에서는 기존 승인 대기 차단이 유지되어야 한다.

## 로컬 검증 결과

- 회원가입 UI/API unit·integration(mock) 42개 통과.
- production 배포 관련 pytest 38개 통과.
- Chromium AUTH-01 E2E 통과: 320/390/412px 입력·필수 동의·선택 동의·가입 요청(mock API).
- TypeScript 및 Vite production build, Frontend lint 통과. 번들 500kB 경고 있음.
- 전체 Ruff check/format, Mypy(758개 소스), Python test inventory, `git diff --check` 통과.
- pnpm 실행은 공유 node_modules 재설치를 요구해 중단되어 설치된 실행 파일로 검증했다.
- 전체 DB/Worker test runner, 실제 SMTP·운영 DB 가입 및 Docker 이미지 배포는 실행하지 않았다.
