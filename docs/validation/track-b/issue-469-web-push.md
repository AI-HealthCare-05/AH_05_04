# #469 Web Push 검증 기록

상태: 구현 PR 검토 중 · 책임 리뷰 및 실기기 통합 대기.

- 구현 담당: 권가빈 (`hazelnutflavoured`).
- 책임 리뷰: 송은영 (`phina-io`) — Backend/DB/Security, 남한솔 (`solia142`) — Frontend 소비 계약.
- 브랜치: `feat/469-web-push`, 검증 코드 commit: `563a9777c5fd120e78eaf11df56e9fb11323c66c`. 이후 commit은 검증·운영 문서 정리만 포함한다.
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

- 첫 전체 CI Backend 결과: 2012 passed / 85 skipped / 1 failed. Runtime 역할의 새 Push 테이블 권한 누락을 확인해 `provision_database_roles.py`에 Push DML을 추가했다. Source Writer/Management 권한은 확대하지 않는다.
- 최신 develop `f10ca016`을 rebase하고 migration parent를 `166f30415263`으로 연결했다.

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

최신 develop `f10ca016`, 코드 commit `563a9777c5fd120e78eaf11df56e9fb11323c66c` 기준.

- 최신 Push/migration/Runtime 권한·인증 재검증: **73 passed**.
- 두 번째 전체 검사: migration 213 passed/4 skipped, Worker 3109 passed/8 skipped, Backend 2019 passed/85 skipped/1 failed. 과거 Source cutover fixture가 당시 없던 Push 테이블을 grant하려는 문제를 수정했다. 실제 현재 Runtime 권한은 유지했고 Source Writer 접근·Runtime TRUNCATE 거절 테스트를 추가했다.
- 최종 `bash scripts/ci/run_test.sh`: **PASS (exit 0)**.
  - migration: 213 passed / 4 skipped
  - Backend·계약·PostgreSQL: 2020 passed / 85 skipped
  - Redis 통합: 24 passed
  - Worker: 3109 passed / 8 skipped
  - 합계 5366 passed / 97 skipped, combined coverage 92%
  - skip은 기존 별도 승인·선택 실행 대상이며 완료/통과로 계산하지 않는다.
- `uv run ruff check .`: PASS.
- `uv run ruff format . --check`: PASS (805 files).
- `uv run mypy backend/app ai_worker`: PASS (610 source files).
- 최종 CI는 #469 전용 Compose/test DB에서 실행했다. 실행 명령은
  `ENV_FILE=/private/tmp/ah469-test.env COMPOSE_FILE=/private/tmp/ah469-compose.yml bash scripts/ci/run_test.sh`.
  환경파일은 합성 테스트 설정만 포함하는 로컬 파일이며 저장소에 추가하지 않았다.
- 의존성 검증: 기존 패키지 버전 변경 0개; pywebpush 및 전이 의존성만 추가.
- 문서 Markdown 렌더·상대 링크·git diff --check 검증 통과.
