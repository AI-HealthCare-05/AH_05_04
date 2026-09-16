# Production 회원가입 복구

기준: `dac51c29`. 이 PR은 회원가입 build-time 설정 전달 경로를 준비한다.
작업 대화에서 승인 완료라는 요청자 확인을 받았으나, 이는 재현 가능한 최종 승인 artifact가 아니다.
[외부 승인 provenance 기준](../release-gates/post-mvp-1-external-approvals.md)에 따라
승인 대상 commit/문구 version, 승인 범위, 승인자 역할, 승인 시각과 제한 조건을 갖춘 artifact를
연결하기 전에는 `VITE_SIGNUP_TERMS_APPROVED=false`를 유지한다.
현재 해당 artifact는 제공되지 않았으며 이 PR은 최종 승인이나 Production 가입 활성화를 선언하지 않는다.
Track C/F 및 공통 Privacy Production gate도 변경하지 않는다.

중복확인 버튼은 미구현 상태로 disabled가 고정되어 있었다. 가입 시 기존 API의
이메일 중복 검증을 사용하며 별도 중복확인 API를 추가하지 않는다.
약관 승인 flag가 빌드에 전달되지 않아 필수 동의와 가입 버튼도 비활성화되어 있었다.

## 배포

운영 환경파일에는 `VITE_SIGNUP_TERMS_APPROVED=false`를 명시한다.
배포 스크립트는 환경파일의 선언 누락과 true/false 이외의 값을 외부 작업 전에 거부하며,
실행 셸에 상속된 true로 누락을 대체하지 않는다. false는 배포할 수 있으나 회원가입은 차단된다.
Docker 직접 빌드의 기본값도 false다. 위 승인 artifact가 연결되고 정확한 배포 문구와 일치함을
확인한 뒤에만 운영 환경파일에 true를 명시하여 새 고정 이미지 버전으로 빌드한다.

`VITE_EMAIL_VERIFICATION_ENABLED`는 실제 이메일 발송 준비 상태 및 Backend의
`SIGNUP_EMAIL_VERIFICATION_REQUIRED`와 맞춘다. 이 변경은 이메일 인증을 자동 활성화하지 않는다.
Vite 값은 빌드 시 정적 번들에 포함되므로 서버 환경변수 수정이나 컨테이너 재시작만으로는 적용되지 않는다.
Frontend 이미지를 재빌드하고 해당 이미지로 nginx 서비스를 갱신해야 한다.

## 승인 artifact 확보 후 explicit true 빌드 확인

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

## PR #665 리뷰 반영 검증

- Docker 및 예제 환경파일 기본값 false, 배포 환경파일 명시 선언 및 true/false 검증.
- 선언 누락·빈 값·TRUE·1·yes 거부, 상속된 true로 누락을 대체하지 않는 테스트 포함.
- false는 승인 대기를 유지하고 explicit true에서만 기존 동의·가입 흐름 사용.
- production 관련 pytest 45개, 회원가입 UI/API 테스트 45개 통과.
- TypeScript, Frontend lint, 변경 Python Ruff check/format, shell syntax, diff check 통과.
- 초기 누락값 테스트에서 Bash 변수명과 한국어 조사 경계를 발견하여 중괄호로 수정 후 재검증.
- 운영 환경파일·승인 artifact는 변경하지 않았으며 실제 가입 활성화·배포하지 않음.
