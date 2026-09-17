# Production 회원가입 복구

2026-09-17 PM이 현재 약관을 사용하는 회원가입을 **30일 데모 한정** 승인했다.
[승인 범위·대상·기간·담당자](../governance/decisions/2026-09-17-signup-demo-approval.md)를 따른다.
이는 기존 #665의 승인 미제공 상태를 데모 범위에서 갱신하며, 최종 외부 법무 승인이나
Track C/F 및 공통 Privacy Production gate 해제를 의미하지 않는다.

중복확인 버튼은 미구현 상태로 disabled가 고정되어 있었다. 가입 시 기존 API의
이메일 중복 검증을 사용하며 별도 중복확인 API를 추가하지 않는다.
약관 승인 flag가 빌드에 전달되지 않아 필수 동의와 가입 버튼도 비활성화되어 있었다.

## 배포

데모 기간에는 실제 운영 환경파일과 `envs/example.prod.env`에
`VITE_SIGNUP_TERMS_APPROVED=true`를 명시한다. 예제 변경만으로 기존 운영 파일은 바뀌지 않는다.
배포 스크립트는 환경파일의 선언 누락과 true/false 이외의 값을 거부한다.
Docker 직접 빌드의 기본값은 false를 유지하고 운영 파일의 explicit true를 build arg로 전달한다.
2026-10-17 00:00 KST에 데모가 종료되므로 그 전에 종료 배포를 준비한다.
자동 만료는 없으며 종료 시 false 설정으로 재빌드·배포해야 한다.

`VITE_EMAIL_VERIFICATION_ENABLED`는 실제 이메일 발송 준비 상태 및 Backend의
`SIGNUP_EMAIL_VERIFICATION_REQUIRED`와 맞춘다. 이 변경은 이메일 인증을 자동 활성화하지 않는다.
Vite 값은 빌드 시 정적 번들에 포함되므로 서버 환경변수 수정이나 컨테이너 재시작만으로는 적용되지 않는다.
Frontend 이미지를 재빌드하고 해당 이미지로 nginx 서비스를 갱신해야 한다.

## 데모 승인에 따른 explicit true 빌드 확인

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
