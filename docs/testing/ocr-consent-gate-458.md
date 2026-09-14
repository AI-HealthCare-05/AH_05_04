# #458 동의 조회·재검사 검증 — 2026-09-14

## 기준과 범위

- develop `ac1b148a`, 로컬 동기화 `71131950` 이후 변경.
- #465 미병합 기준 HEAD `6d5494cc0a488675a195cd5db71bd5e50a49fd82`.
- [구현 범위·담당자 답변·후속 연결](../designs/ocr-consent-gate-458-implementation.md).
- 외부 Provider 호출 없이 합성 데이터·전용 임시 PostgreSQL 17로 실행.

## 집중 검증

- OCR 전체: 66 passed. 신규 Gate 검사 17건 포함.
- 실제 PostgreSQL adapter: 12 passed. 별도 transaction의 철회/재동의 가시성, 잘못된 Job·소유권,
  다른 사용자/목적의 동의 오사용 차단, 누락/버전 불일치/timestamp 오류/조회 실패 검사.
- #465 공통 fixture 8개 판정과 일치. Worker에서는 같은 판정에 실제 호출 대역을 붙여 차단 시 0회 확인.
- CLOVA 완료 후 철회/조회 장애/정책 변경 시 LLM 호출 0회.
- 실제 ClovaOcrEngine의 구조화 지점에 guard를 조립한 합성 테스트 통과. runtime factory 연결 증빙은 아님.
- DB 예외 chain·원문을 반환하지 않고 asyncio 취소를 전파.
- Ruff check/format·Mypy(615 files) 통과.

## 전체 스크립트 차단 — develop 기준 문제

`scripts/ci/run_test.sh`는 단일 migration head 사전 검사에서 exit 1:

- `178a1b2c3d4e`: Knowledge Evidence Index (#178 / PR #482)
- `192a1b2c3d4e`: Track C 저장 (#192 / PR #310)

원격 develop 파일을 git show로 읽어 DAG를 별도로 계산했으며 같은 두 head를 확인했다.
#458의 backend/alembic/versions 변경은 0건이다. 이미 병합된 migration의 down_revision을
고치거나 full-CI의 검사를 우회하지 않았다. 공유 develop migration 분기는 별도 수정이 필요하다.
따라서 Backend/전체 migration/Redis 통합 전체 통과를 주장하지 않는다.

처음 기존 다른 작업의 venv에는 최신 develop의 pgvector가 없어 Mypy import 오류가 있었다.
#458 전용 venv를 기존 lockfile로 구성한 뒤 통과했다. dependency/lockfile 변경은 없다.
초기 전체 실행의 신규 테스트 미분류는 explicit opt-in 등록으로 해결했다.

## 집중 DB 검사 재현

운영·개발 DB 대신 테스트 전용 PostgreSQL URL을 설정하고 다음을 실행한다.
`OCR_CONSENT_TEST_DATABASE_URL`은 테스트를 위해 schema 생성/삭제가 허용된 격리 DB만 지정한다.

```bash
PYTHONPATH=backend:. uv run pytest tests/integration/test_worker_ocr_consent.py -q
```

URL이 없으면 명시적으로 skip한다. schema 이름은 실행마다 `consent458_<uuid>`로 분리하고
해당 schema만 제거한다. 최소 물리 컬럼 fixture이며 #465 migration 전체 검증을 대신하지 않는다.
실제 동의 API·runtime 차단 저장·Frontend·payload 최소화·사용자 대상 활성화는 미검증이다.

## Worker 전체·검사 결과

- Worker 전체: 3197 passed, 8 skipped, 1 failed (40.66s).
- 실패: `test_committed_infrastructure_evidence_matches_builder`.
  `protected-runner-infrastructure-adapter.md`의 runtime_assembly.py hash가 생성 결과와 다름.
  해당 증빙·코드의 origin/develop 원문만 임시 디렉터리에 복원해 같은 불일치를 재현했다.
  #458이 변경한 파일이 아니며, 자동 생성 증빙 수정은 별도 #487/#488 후속 범위로 남긴다.
- Test inventory 회귀: 13 passed.
- DB 함수/프로시저/Trigger/RLS 재도입 검사·보호 테이블 쓰기 검사 통과.
- 실제 PostgreSQL 집중 12건은 전용 venv와 최신 CI의 pgvector PostgreSQL 17 이미지에서도 재통과.

두 기존 develop 문제(분기 head, 증빙 Markdown 불일치)를 별도로 수정하기 전 전체 CI 성공으로
표시하지 않는다. #458 신규 테스트의 실패는 없으며 위 실패를 제외해 전체 성공으로 재표기하지 않는다.
