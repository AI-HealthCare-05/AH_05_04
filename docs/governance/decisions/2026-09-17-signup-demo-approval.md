# 30일 데모 회원가입 활성화 승인

- Issue: [#687](https://github.com/AI-HealthCare-05/AH_05_04/issues/687)
- 승인 기록일: 2026-09-17 (Asia/Seoul)
- 승인자: 권가빈 (PM)
- 구현 담당: 권가빈
- 단일 담당 리뷰어: 송은영
- 기준 commit: `9c82040b`
- 약관 대상: `frontend/src/pages/TermsOfService.tsx` (시행일 2026-09-15)
- 대상 파일 SHA-256: `736b38f000addfc4cf1e8131f3d96a0e69924afa102690ed3bcd841be1ad382b`

## 범위와 기간

PM은 현재 약관을 사용하는 회원가입의 Local 및 Production 데모 활성화를 30일 한정 승인했다.
날짜 기준 운영 기간은 2026-09-17 00:00 KST 이상, 2026-10-17 00:00 KST 미만으로 기록한다.
`VITE_SIGNUP_TERMS_APPROVED=true`를 명시적으로 전달하여 필수 약관 동의 후 가입을 허용한다.
이 기록은 PM의 제한적 데모 승인이다. 최종 외부 법무 승인이나 Track C/F·공통 Privacy Production
공개 게이트 해제 증빙으로 확대 해석하지 않는다. 약관 본문·API·DB·동의 목적은 변경하지 않는다.

## 적용과 종료

- 운영 환경파일의 `VITE_SIGNUP_TERMS_APPROVED=true`를 확인하고 Frontend 이미지를 새 버전으로 빌드·배포한다.
- 예제 운영 환경파일에도 같은 값을 명시해 다음 데모 배포 시 설정을 보존한다.
- Docker 직접 빌드 및 설정 누락의 기본 차단은 유지한다.
- 담당 PM은 종료 시 운영 환경파일과 예제의 값을 `false`로 되돌리고 Frontend를 재빌드·배포한다.
- 현재 boolean 플래그에는 자동 만료 기능이 없다. 이 문서만으로 배포된 번들이 자동으로 종료되지는 않는다.
- 연장·상시 공개는 별도 승인이 필요하다.

## 검증

- 회원가입 UI/API mock 회귀 37개 통과 (`VITE_API_BASE_URL=http://localhost:8000`).
- 약관 빌드 전달·Production 배포·remote shell 검사 40개 통과.
- 14176 Local Vite 응답에서 `VITE_SIGNUP_TERMS_APPROVED=true` 확인.
- Production 원격 배포 및 실사용자 가입은 이 변경에서 실행하지 않음.
