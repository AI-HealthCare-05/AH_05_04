# #469 Web Push 검증 기록

상태: 구현 PR 검토 중 · 책임 리뷰 및 실기기 통합 대기.

- 구현 담당: 권가빈 (`hazelnutflavoured`).
- 책임 리뷰: 송은영 (`phina-io`) — Backend/DB/Security, 남한솔 (`solia142`) — Frontend 소비 계약.
- 브랜치: `feat/469-web-push`. 기준 develop과 최종 commit은 PR commit에서 확인한다.
- 환경: macOS arm64, Python 3.13.9, PostgreSQL 17 임시 컨테이너, Redis 임시 컨테이너.
- DB는 #469 전용 테스트 서비스의 `test` 및 테스트별 임시 DB/schema. 실제 사용자 데이터·외부 Push endpoint·VAPID 운영키 미사용.
- Migration: `469a1b2c3d4e`. 실제 upgrade/제약/downgrade 거절/빈 테이블 rollback/재upgrade 검증.
- 합성 fixture: `backend/app/tests/push/conftest.py`의 테스트 실행 시 생성 P-256/Fernet 키, `push.example.test`, 합성 auth. 키는 테스트 종료 후 폐기하고 출력하지 않는다.

## 실행 결과

- 최초 Push API/DB 테스트: 25 pass, 2 fail. 테스트 fixture의 해제 시각과 rollback 뒤 타인 계정 보존 가정을 수정했다.
- Push + 기존 Notification 회귀: 100 passed.
- 동시성·migration을 추가한 Push 전용 실행: 63 passed.
- Ruff check/format 및 Mypy: 통과. 최종 전체 검사 결과는 아래 최종 검증 절에 기록한다.
- 첫 전체 CI 실행의 Worker 환경 검사: 임시 환경파일 REDIS_HOST가 127.0.0.1이라 승인 기본값 redis 검사 1건 실패. 테스트 코드 변경 없이 환경파일을 기본값으로 정정했다. Redis 실제 연결은 기존 runner의 전용 integration override를 사용한다.

## 검증한 경계

API 인증·SELF 소유권·타인 404·중복 등록·여러 기기·세대 변경·해제·no-store·민감 입력 비반사,
실제 PostgreSQL의 UNIQUE/동시 등록/단일 전송 선점/취소와 기한 재검사,
timeout/crash UNKNOWN·늦은 응답 fencing·재시도 상한/Retry-After·만료 구독 키 삭제,
앱 내부 게시/읽음/Check-in 불변성을 검증한다.
합성 receiver private key로 aes128gcm payload를 복호화하고 ES256 VAPID JWT의 audience/signature를 확인한다.
SSRF 테스트는 URL/공인 DNS 전체 검사, 검증 IP의 TCP 연결 및 원래 TLS hostname, redirect/proxy 미사용을 확인한다.

## 미실행 및 승인 경계

- 실제 외부 Push 서비스 접수, iOS 홈 화면/Android 기기 표시: NOT_RUN (#471).
- #470 Service Worker generation 비교·로그아웃/계정 전환·클릭 후 원래 날짜 표시: 통합 대기.
- 실제 회원탈퇴 API는 기존 계정 생명주기 후속 범위. 비활성 계정 전송 차단 및 FK cascade와 탈퇴 전체 완료를 구분한다.
- Backend mock·암호화 roundtrip은 실제 기기 수신 또는 개인정보 외부 전송 승인 증빙이 아니다.
- Production 등록·전송은 코드로 차단하며 책임 리뷰/Privacy 확인은 아직 받지 않았다.

## 최종 검증

최신 develop 반영 후 실행 결과 기록 예정.
