# #202 Check-in API 검증

- 구현 담당: 권가빈 (`hazelnutflavoured`)
- 책임 리뷰: 송은영 (`phina-io`), Frontend 소비 계약 남한솔 (`solia142`)
- 기준: 2026-09-10, `origin/develop` `99bb259`에서 시작한 `feat/202-medication-api`
- 상태: Draft 부분 구현·지정 리뷰어 승인 대기. Issue #202 전체 완료가 아니다.
- 계약: [Check-in target](../../contracts/targets/post-mvp-1/checkin-v1.md),
  [HTTP Decision 제안](../../governance/decisions/2026-09-10-checkin-api-202.md)

## 검증 환경

Python 3.13, PostgreSQL 17 Alpine, Redis 7 Alpine.
실제 환자 데이터·외부 Provider 호출 없이 기존 합성 fixture로 검증한다.
전용 Compose project의 loopback 동적 포트·tmpfs PostgreSQL과 `test` DB를 사용했다.
개발·운영 DB 및 기존 작업 폴더의 미커밋 문서 변경은 사용하지 않았다.
새 migration·DB trigger·의존성·배포 설정 변경은 없다.

## 검증 결과

| 검사 | 결과 |
| --- | --- |
| Check-in API + B3 서비스·Repository 관련 pytest | 34 passed |
| `uv run ruff check .` | PASS |
| `uv run ruff format . --check` | PASS — 615 files |
| `uv run mypy backend/app ai_worker` | PASS — 507 source files |
| `bash scripts/ci/run_test.sh` | PASS — 4,320 passed, 67 skipped; coverage 94% |
| Alembic heads | PASS — `201a1b2c3d4e` 단일 head, 신규 migration 없음 |
| Markdown 로컬 링크 | PASS |
| `git diff --check` | PASS |

로컬에서는 기존 설치 runtime을 `uv run --no-sync`로 재사용했다. 전체 runner에는
`ENV_FILE`, `COMPOSE_FILE`로 전용 합성 테스트 환경을 전달했다. Migration 144 passed,
Backend·계약 1,565 passed/59 skipped, 선별 Redis 통합 23 passed, Worker 2,588 passed/8 skipped다.
67개 skip을 통과 건수에 합산하지 않았다. GitHub CI 결과는 PR의 Checks에서 별도로 확인한다.

## 핵심 증빙

`backend/app/tests/medication_checkins/test_medication_checkin_api.py`는 실제 ASGI 앱과
DB 의존성을 통과해 아래 사항을 검증한다. 로그인 사용자만 합성 fixture로 주입한다.

- `TAKEN`·`NOT_TAKEN` 생성, 현재값 정정, Audit append, revision·corrected 표시
- 최초 snapshot 재현: 이후 revision이 전진해도 최초 status·body가 동일
- 동일 키·다른 지문 409, 새 키·오래된 revision 409 및 미저장
- 두 독립 DB session이 동시에 최초 멱등 조회를 마친 상황에서 같은 키 요청 둘 다
  동일 200 응답, 현재 Check-in 1개·idempotency record 1개만 commit
- Scheduler `UNCONFIRMED`를 사용자 결과로 정정하며 감사 이력 보존
- 사용자 UNCONFIRMED 제출 전용 422, SKIPPED·reason_code·잘못된 revision·taken_at 거부
- UTC 정규화와 같은 instant의 다른 timezone 표기 재현
- 취소 occurrence 409, 없는 ID·타 사용자 ID 동일 404, 인증 없는 요청 401
- snapshot 크기 제한 오류 시 Check-in·occurrence·idempotency 원자적 rollback
- 실제 응답 DTO 파싱, OpenAPI request enum·필수 헤더·공통 오류 schema
- no-store·trace 응답 헤더

기존 B3 서비스·Repository 테스트도 같은 실행에서 통과했다. 최초 테스트 실행에서
오류 응답 후 test session rollback이 ORM fixture를 만료시켜 2개 테스트가 실패했다.
요청 경로 ID와 인증 fixture를 immutable 값으로 유지하도록 테스트를 수정한 뒤 재검증했다.
앱 구현을 우회하거나 실패 assertion을 제거하지 않았다.

## 미완료·리뷰 사항

날짜별 조회의 `setup_reason` 정책, 일정 PUT/PATCH의 원본 schedule audit 요구와
B1 모델 차이, history 화면 범위 및 backlog 상세 URL·pagination은 미확정이다.
따라서 해당 route는 추가하지 않았으며 #202를 자동 종료하지 않는다.

Track C 실제 무효화 adapter는 #195 연결 전 B3의 명시적 no-op 상태다.
이번 검증은 Track C E2E, Frontend #138, Notification #203, 외부 Privacy 승인 또는
Production 공개 검증을 대체하지 않는다. AI/RAG 생성 코드 변경이 없어 별도 Provider
평가·의료 AI live eval은 실행하지 않았다.
