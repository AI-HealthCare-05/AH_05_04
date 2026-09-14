# #458 동의 Gate·차단 저장 검증 — 2026-09-14

## 기준

- develop `a440d2ae` (#465 병합), 로컬 동기화 merge `3a77d956`.
- [구현과 답변 근거](../designs/ocr-consent-gate-458-implementation.md),
  [사유·상태·transaction 결정안](../governance/decisions/2026-09-14-ocr-consent-worker-458.md).
- 합성 데이터와 전용 PostgreSQL 17/Redis를 사용한다. CLOVA/OpenAI 호출은 대역으로 검증한다.

## 확인한 동작

- 병합된 #465 공통 fixture 8건과 같은 판정. 원본 fixture 변경 없음.
- CLOVA·LLM 직전 최신 동의 조회. 누락·철회·버전 불일치·조회 실패·계정/소유권 불일치이면 호출 차단.
- CLOVA 중 철회·조회 실패·policy 변경 시 LLM 호출 0회. LLM 비활성 경로도 결과 반환 전 재검사.
- SQLAlchemy Core 조회로 Backend ORM/Service import 경계를 유지한다.
- Dispatcher는 동의 차단을 일반 INTERNAL_ERROR로 바꾸지 않는다.
- 현재 lease/event/attempt/token에 결속된 Job STALE, Attempt BLOCKED, OCR FAILED와 명시적 사유 저장.
- 일부 저장 실패 시 전체 rollback. commit 실패 또는 lease 상실 시 성공 필드 저장·ACK 없음.
- 차단 상태 commit 이후 ACK. 같은 event 재전달은 이미 소비한 것으로 처리하며 새 Attempt 없음.
- 유효 동의의 실제 Worker runtime·Redis·PostgreSQL 성공 경로 유지. 외부 Provider만 대역이다.
- 기존 처방일 필수 검수 및 처방 확정 계약 변경 없음.

## 검사 결과

- 집중 Worker/Adapter/Consumer 테스트: **147 passed**.
- Ruff check 및 format check 통과. Mypy **618 source files** 통과.
- 전체 migration: **227 passed, 4 skipped**. 최신 단일 head `207c1d2e3f4a` 검증 통과.
- 최신 DB의 Trigger/RLS/제거 대상 함수 **0개**, 재도입 검사·보호 테이블 쓰기 검사 통과.
- 전체 `scripts/ci/run_test.sh`: exit 0. **5461 passed, 97 skipped**, coverage **92%**.
  - Backend·계약·PostgreSQL: 1996 passed, 85 skipped.
  - Redis 선별 통합: 29 passed.
  - Worker: 3209 passed, 8 skipped.
  - 위 migration 227 passed, 4 skipped 포함.
- 전체 CI 이후 AI Job 사용자·문서 업로더·SELF 소유자 일치 검사를 보강했다.
  최종 관련 DB·Redis 재검증: **25 passed** (동의 조회 13, 작업 저장/runtime 12).
  Ruff·format·Mypy도 최종 코드 기준 재검증한다.

이전 실행의 migration 분기와 보호 증빙 해시 불일치는 최신 develop에서 해소됐다. origin/develop을
독립 임시 디렉터리에 복원해 보호 증빙 검사 통과를 확인했다. 이번 Config/runtime 변경에 따른
해시는 병합된 `scripts/verify_protected_runner_evidence.py --write`로 갱신했다. JSON/Markdown에서
변경된 것은 두 구현 파일의 hash와 증빙 self hash이며 승인·활성화 상태는 바꾸지 않았다.
갱신 후 증빙 회귀 4건 통과. 갱신 전 Worker 실행은 3207 passed, 8 skipped, 이 해시 검사 1 failed였다.

## DB 집중 검사

```bash
PYTHONPATH=backend:. uv run pytest tests/integration/test_worker_ocr_consent.py -q
```

`OCR_CONSENT_TEST_DATABASE_URL`은 전용 합성 테스트 DB만 지정한다. 미설정이면 명시적으로 skip한다.
검사는 매번 `consent458_<uuid>` schema를 만들고 제거한다. 이 fixture는 조회에 필요한 물리 컬럼의
격리 검사다. #465의 전체 migration 검증을 대신하지 않으며 전체 CI에서 별도로 검증한다.

`tests/integration/test_worker_job_execution_repository.py`의 실제 DB 차단 저장·부분 실패 rollback·
재전달·Redis runtime 테스트는 전체 CI Backend lane에 등록돼 있다. DB를 재생성하는 전체 CI와
별도 DB 테스트를 동시에 시작하면 안 된다. 첫 집중 재실행의 23 passed/1 error는 전체 CI의 DB
재생성과 겹친 실행 오류였으며 해당 실행을 최종 성공 증빙으로 사용하지 않는다.

## 아직 증명하지 않은 범위

전송 payload 최소화·LLM 생략 metadata/DTO·Backend 동의 API/접수 Gate·과거 결과 접근 차단·
Frontend 연결은 이 검사로 완료됐다고 주장하지 않는다. 현재 LLM 전체-token 경로의 대체 및
최종 동의 문구/버전과 실제 사용자 공개 조건은 남아 있다. 환경 활성화나 실제 사용자 전송은 하지 않았다.
