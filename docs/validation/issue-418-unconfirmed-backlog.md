# #418 UNCONFIRMED backlog 후보 구현 검증

검증일: 2026-09-10. Base: `origin/develop`의 `ebcb21e`. Branch: `feat/418-unconfirmed-backlog`.

## 최초 후보 구현 결과

| 검사 | 결과 |
| --- | --- |
| `uv run ruff check .` | PASS |
| `uv run ruff format . --check` | PASS, 621 files |
| `uv run mypy backend/app ai_worker` | PASS, 513 source files |
| `bash scripts/ci/run_test.sh` | PASS, exit 0 |
| Migration | 144 passed |
| Backend/Contract/selected PostgreSQL | 1547 passed, 59 skipped |
| Redis integration | 23 passed |
| Worker | 2633 passed, 8 skipped |
| 전체 합계/coverage | 4347 passed, 67 skipped / 94% |
| 최종 OpenAPI 오류 명세·잘못된 token 테스트 보완 후 관련 PostgreSQL 재검증 | 7 passed |
| `git diff --check`, 전체 diff, 새 계약/Decision 상대 링크 | PASS |

전용 PostgreSQL 17·Redis 7 컨테이너와 합성 credential/fixture를 사용했다. 테스트 파일은 `backend/app/tests/repositories/test_medication_checkin_backlog_integration.py`와 기존 `test_medication_checkin_repository_integration.py`다. 전체 suite 이후 변경은 OpenAPI 오류 응답 모델 명시 및 인증/스키마 테스트·문서 보완이며, 이 변경은 관련 테스트 재실행과 Ruff/Mypy로 검증했다. 실제 Provider/환자 데이터나 공유 개발 DB를 사용하지 않았다.

## 이전 등록 HEAD의 검증 기록 (현재 등록은 철회됨)

2026-09-10, `039c54c` 이후 변경. #413의 병합된 Check-in PUT을 재사용하고 v1 router에 backlog GET을 등록했다. 초기 테스트 전용 앱을 실제 `app`으로 전환했다.

- Cursor 404는 빈 목록이 아니며 Frontend가 cursor를 생략해 첫 페이지를 재조회하도록 Proposed 계약과 PD-418에 명시했다.
- 실제 앱의 인증·SELF ownership·오류·no-store·OpenAPI와 PUT 보완 후 GET 제외, replay, stale revision 충돌 후 재조회, corrected cursor 연속성 및 404 후 cursor 없는 재요청을 검증했다.
- 첫 실행은 27 passed / 3 failed: 실제 HTTP 오류 rollback 뒤 ORM fixture 속성의 지연 로딩이 실패했다. Seed를 commit하고 비교용 ID를 요청 전에 보존해 수정했다. 최종 관련 두 파일은 **30 passed**다.
- Ruff check·format: PASS (626 files), Mypy: PASS (517 source files).
- `ENV_FILE=envs/example.local.env COMPOSE_FILE=/private/tmp/pr426-test-compose.yml bash scripts/ci/run_test.sh`: PASS, exit 0. Migration 144, Backend/Contract 1578, Redis integration 23, Worker 2633 passed — **4378 passed, 67 skipped, coverage 94%**.
- Pandoc HTML 렌더링의 제목·표·목록, 상대 링크, 전체 diff 및 `git diff --check`를 검토했다.
- 위 전체 suite 통과 후 원격 `c0cf755e`에 포함된 develop 병합(#414/#424)을 통합했다. 최종 통합 상태는 backlog·PUT **30 passed**, 새 Guideline Card **48 passed**, Ruff check/format **PASS (629 files)**, Mypy **PASS (520 source files)**다. 전체 suite 수치는 원격 통합 전 실행이며, 통합 후에는 영향 테스트를 재실행했다.
- 전용 `pr426-review-tests` PostgreSQL 17·Redis 7 및 예제 합성 설정을 사용했다. 개발/운영 DB·실제 Provider는 사용하지 않았다.

## 승인 전 라우터 등록 blocker 반영

2026-09-10, `b213cc01`의 Backend blocker에 따라 실제 v1 router 등록을 철회하고 미등록 docstring을 복원했다. 위 실제 앱 통합 기록은 이전 HEAD의 실행 이력이며 현재 공개 상태를 뜻하지 않는다.

- 실제 앱 route 목록·OpenAPI에서 backlog 경로가 없고 HTTP 404임을 회귀 테스트로 확인한다.
- 테스트 앱에만 backlog router를 등록하고, 실제 `v1_routers`의 #413 PUT과 공통 middleware·DB dependency로 보완·replay·revision 충돌·cursor 복구를 검증한다. PUT 서비스나 repository를 mock으로 대체하지 않는다.
- 관련 backlog·Check-in PUT HTTP 검사: **31 passed**.
- Ruff check/format: **PASS (637 files)**, Mypy: **PASS (525 source files)**.
- 전체 필수 검사: **4454 passed, 67 skipped, coverage 94%, exit 0** (Migration 148 / Backend·Contract 1602 / Redis integration 23 / Worker 2681).
- 예제 합성 설정 및 전용 임시 PostgreSQL 17·Redis 7 사용. 실제 Provider·환자정보·개발 DB 사용 없음.

## 검증 범위와 남은 조건

- SELF 소유 목록, 동일 예정 시각의 ID 정렬, cursor pagination, 앞 페이지 보완 후 다음 페이지 연속성
- 이전 처방 버전의 snapshot, 현재 revision, TAKEN/NOT_TAKEN 보완 후 제외, stale revision 409와 재조회
- 조회 무변경, 인증 누락/무효, 타인/없는 cursor, limit·UUID 검증, 공통 오류/no-store
- 실제 앱 route/OpenAPI 미등록·HTTP 404 및 테스트 앱의 #413 PUT→GET 통합

[PD-418](../governance/decisions/2026-09-10-unconfirmed-backlog-418.md) 및 [Proposed 계약](../contracts/proposed/unconfirmed-backlog-v1.md)은 Backend/Frontend 승인 대기다. 실제 앱 등록은 보류하며 테스트 앱에서만 #413 PUT-GET HTTP 연동을 검증했다. 두 담당 리뷰어의 계약 승인 후 등록·실제 앱 검증을 추가하고 해당 HEAD의 승인을 받아야 한다. #138 Frontend 소비 검증과 계약 상태 전환 전에는 #418 완료로 판정하지 않는다. 새 migration과 Frontend 구현은 없다.


## #429 병합 후 CI fixture 호환성 수정

`6a6939eb`의 CI에서 `test_historical_snapshot_and_refetch_after_stale_revision`이 `medication_count` NOT NULL 위반으로 실패했다. #429에서 필수가 된 `medication_count`·`content_hash`를 기존 replacement fixture가 누락한 것이 원인이다.

공용 `fingerprint_values`로 교체 버전의 fingerprint를 계산하고 같은 합성 약품 데이터로 `PrescriptionVersionMedication` 행을 생성한다. 필수 컬럼만 임의 값으로 채우거나 DB 제약을 완화하지 않는다. 기존 과거 version snapshot·stale revision 검증과 승인 전 router 미등록 경계는 유지한다.

- backlog·Check-in repository·PUT HTTP: **34 passed** (전용 임시 PostgreSQL, 합성 설정).
- Ruff check/format: **PASS (683 files)**, Mypy: **PASS (539 source files)**.
- `git diff --check`: PASS. 전체 CI는 이 수정 커밋의 GitHub Actions 결과로 확인한다.
