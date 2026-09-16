# #194 Follow-up 검증 기록

## 범위와 환경

- 구현 담당: 권가빈 (@hazelnutflavoured).
- 단일 책임 리뷰어: 김지혜 (@Jye-rookie) — Backend·API/DTO·Transaction·Security·Frontend 소비 계약.
- 기준: develop `a542bcc2` (PR #618 포함), [PD-194-2](../governance/decisions/2026-09-16-track-c-followup-194.md).
- 합성 계정·처방·응답만 사용했다. 전용 PostgreSQL 17/pgvector tmpfs와 Redis 7을 loopback의 별도 port로 실행했다.
- 기존 개발 환경과 #139의 작업 폴더·DB는 사용하지 않았다. 외부 Provider 호출·Frontend 변경은 없다.
- Python 3.13.9, 기존 잠금 dependency 환경을 사용했다. dependency 변경·새 migration은 없다.

## 집중 검증

| 검사 | 결과 |
| --- | --- |
| 신규 Follow-up API | 28 passed |
| Track C 전체·정정 무효화·HandlerConfig·기존 합성 계약 fixture | 175 passed (신규 API 28건과 동시성 6건 포함) |
| 신규 Frontend 인계 fixture DTO 검사 | 1 passed |
| Ruff check / format | PASS |
| Mypy Backend·Worker | PASS, 737 source files |

집중 검증 명령:

```bash
uv run --group app --group dev pytest \
  backend/app/tests/track_c \
  backend/app/tests/services/test_track_c_revision_invalidation.py \
  backend/app/tests/repositories/test_track_c_revision_invalidation_integration.py \
  tests/services/test_track_c_handler_config.py \
  tests/services/test_track_c_operational_config.py \
  tests/contract/test_track_c_support_fixture.py -q
uv run --group app --group dev pytest tests/contract/test_track_c_followup_fixture.py -q
```

## 확인한 동작

- 미응답 GET은 null이며 평가·멱등 row를 생성하지 않는다.
- 세 응답의 최초 저장, 정정 revision과 from/to 응답·변경자 audit를 보존한다.
- 최초 성공 replay는 정정 뒤에도 과거 응답이고 GET은 최신값을 읽는다.
- ACTIVE/CANCELLED는 거부하고, 완료 후 Check-in/Safety/Barrier 정정에도 과거 평가를 허용한다.
- SELF 소유권 404는 replay보다 먼저 적용하며 strict revision·미지원 응답·임의 필드를 거부한다.
- snapshot cap 실패 시 신규 row 또는 정정 값·revision·audit·멱등 기록을 함께 rollback하고 재시도가 성공한다.
- 독립 DB session/transaction으로 첫 제출 경쟁, 정정 경쟁, 동일 key replay, 같은 key 다른 body 충돌,
  Check-in 정정 및 Safety 정정 경쟁을 검증했다. 감사 이력 중복과 revision 유실이 없다.
- Provider·활성 config 로딩 없이 과거 계획에 결속된 응답만 저장한다. 기존 Plan 응답은 바뀌지 않는다.
- OpenAPI의 GET/POST, 필수 body·멱등 header와 3종 enum, 합성 소비 fixture를 대조했다.

## 전체 필수 검사

`ENV_FILE=<전용 합성 env> COMPOSE_FILE=<전용 tmpfs compose> bash scripts/ci/run_test.sh`를 실행했다.

- Migration: 235 passed, 4 skipped. 단일 head `583a1b2c3d4f` 및 schema 검증 통과.
- Worker: 3,685 passed.
- Backend·계약·선별 PostgreSQL 통합: 2,626 passed, 128 skipped.
- Redis 통합: 29 passed.
- 합산 coverage: 91% (97,910 statements, 8,407 missed), gate PASS.
- 전체 스크립트 exit 0. Inventory·DB logic·보호 테이블 쓰기 검사도 통과했다.
- skipped는 통과 건수에 포함하지 않는다. 보호된 RAG/Source의 별도 실행 조건이 필요한 검사와
  migration의 기존 skip이 남아 있으며 실제 의료·공개 승인 증빙이 아니다.

Worker 설정에는 승인 기본값 `REDIS_HOST=redis`, `REDIS_PORT=6379`를 사용했다.
실제 Redis 통합은 runner가 전용 Compose의 loopback port로 격리한다.
`CHAT_HISTORY_CONTEXT_ENABLED=false`이며 운영 환경파일·Provider credentials는 사용하지 않았다.

## 최신 develop 반영 후 재검증

- 최종 기준 develop: `3a8cc79e` (#620·#623 포함). 전체 스크립트는 위 `a542bcc2` 기준에서 통과했다.
- 추가된 upstream 변경은 Track F 순수 커널·테스트·문서다. Follow-up 변경을 충돌 없이 rebase했다.
- 최종 Track C·무효화·HandlerConfig·두 합성 fixture 집중 검증: **176 passed**.
- 최종 Ruff check / format PASS (981 files), Mypy PASS (741 source files), git diff --check PASS.
- upstream CI는 [develop 실행](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/35045536923) SUCCESS다.
  이 결과를 이번 PR의 원격 CI 완료로 대신하지 않는다.

## 인수와 공개

실제 Frontend 클릭·브라우저 E2E(#139), 배포·실제 의료 평가·외부 공개 승인은 미실행이다.
계약은 김지혜의 상태 정렬 요청에 따라 같은 구현 PR #631에서 Current 경로로 이동했다.
최종 책임 리뷰 승인·병합은 대기 중이다. #194 전체 이슈는 종료하지 않는다.

## PR #631 책임 리뷰 반영

- 김지혜의 [리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/631#pullrequestreview-5217946195)에 따라 계약을 Current로 이동하고 index·API·schema·Decision·인계 참조를 정렬했다.
- `ACTION_PLAN_STATE_CONFLICT`의 endpoint별 전제조건을 문서화했다. #618은 실제 승인·병합 기록으로 갱신했다.
- 구현 HEAD `ab5321d2`의 [원격 CI](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/35048292456)는 모든 lane과 required check가 SUCCESS다.
- 이번 수정은 문서와 fixture 상태 설명뿐이다. runtime 코드와 fixture payload가 그대로임을 확인했다.
- fixture 계약 검사: 1 passed. 변경 Markdown 9개 HTML 렌더링·상대 링크·전체 diff 검토 및 `git diff --check` PASS. 이전 계약 경로 참조는 남아 있지 않다.
- runtime 변경이 없어 전체 Backend·Worker 회귀 검사는 재실행하지 않았다. 최종 책임 리뷰 승인·병합은 대기 중이다.
