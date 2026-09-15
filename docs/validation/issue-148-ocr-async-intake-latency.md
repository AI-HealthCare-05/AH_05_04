# #148 OCR 접수 동기→비동기 전환 전후 응답 시간 비교

- 측정자: 김지혜 (`Jye-rookie`)
- 범위: `POST /api/v1/documents/{document_id}/ocr-jobs` 접수 응답 시간
- 기준: 전환 전 `4038f7ea`(#282 직전), 전환 후 `b9c3d0cf` develop, 2026-09-15 로컬 측정

## 전환 내용

전환 전에도 HTTP 상태 코드는 `202`였지만 같은 요청 안에서 CLOVA OCR을 호출하고 결과까지 저장했다.
전환 후에는 접수 트랜잭션이 `AI_JOB`, `IDEMPOTENCY_RECORD`, `OUTBOX_EVENT`, `OCR_JOB` placeholder만
생성하고 Provider 호출은 Worker로 넘긴다. 따라서 이 비교의 대상은 상태 코드가 아니라 **접수 응답이
Provider 호출 완료를 기다리는지 여부**다.

## 측정 방법

- ASGI in-process 클라이언트와 로컬 PostgreSQL을 사용한다. 반복마다 새 문서를 업로드한 뒤 접수를 1회
  호출하고, 접수 호출 구간만 `time.perf_counter()`로 계측한다. 조건당 20회 반복한다.
- Provider는 지연을 주입한 stub으로 대체한다(`get_ocr_engine` 의존성 override). 주입 지연은
  0 ms, 500 ms, 2000 ms 세 조건이다.
- 전환 후 조건에서는 stub이 호출되면 즉시 실패하도록 두었다. 세 조건 모두 실패하지 않았으므로 접수
  경로가 Provider를 호출하지 않는다는 점이 함께 확인된다.

## 결과

접수 응답 시간(ms), 조건당 20회.

| 주입 Provider 지연 | 전환 전 p50 | 전환 전 p95 | 전환 후 p50 | 전환 후 p95 |
| --- | --- | --- | --- | --- |
| 0 ms | 11.3 | 12.8 | 21.3 | 40.7 |
| 500 ms | 532.3 | 535.9 | 20.8 | 25.0 |
| 2000 ms | 2032.0 | 2041.5 | 19.2 | 41.9 |

전환 전 최솟값·최댓값은 0 ms 조건에서 9.9 ms / 20.1 ms, 2000 ms 조건에서 2024.2 ms / 2044.6 ms였다.
전환 후 최솟값·최댓값은 0 ms 조건에서 18.3 ms / 68.9 ms, 2000 ms 조건에서 17.1 ms / 54.5 ms였다.

## 해석

1. 전환 전 접수 응답 시간은 Provider 지연에 1:1로 비례한다. 500 ms를 주입하면 p50이 532.3 ms,
   2000 ms를 주입하면 2032.0 ms로 지연분이 그대로 응답 시간에 더해진다.
2. 전환 후 접수 응답 시간은 Provider 지연과 무관하게 p50 19~21 ms로 일정하다. 주입한 지연이
   2000 ms여도 변하지 않는다.
3. Provider 지연이 0에 가까운 구간에서는 전환 후가 약 10 ms 느리다. 접수 트랜잭션이 Job·멱등성·Outbox·
   OCR placeholder 네 row를 함께 쓰기 때문이며, 손익분기는 Provider 왕복 약 10 ms다. 실제 OCR 왕복이
   이 값을 넘는 순간부터 전환이 이득이다.
4. 설정상 CLOVA 호출 timeout은 20초, OCR 동기 요청 전체 deadline은 60초다
   (`CLOVA_OCR_TIMEOUT_SECONDS`, `OCR_REQUEST_DEADLINE_SECONDS`). 전환 전에는 이 값이 접수 응답 시간의
   상한이었고, 전환 후에는 해당 상한이 요청 경로에서 제거됐다.

## 한계

- 로컬 단일 머신의 in-process 측정이라 네트워크 왕복, 동시 부하, 운영 환경 커넥션 풀 경합이 빠져 있다.
  운영 환경 수치가 아니다.
- 주입한 Provider 지연은 실제 CLOVA 관측값이 아니라 비례 관계를 보이기 위한 통제 변수다. 실제 Provider
  latency는 별도 실측이 필요하다.
- 조건당 20회 반복이라 p95는 표본 상위 한 건에 좌우된다. 전환 후 0 ms 조건의 p95 40.7 ms는 첫 회
  워밍업 영향이 남은 값이며 p50과 함께 읽어야 한다.
- 이 기록은 OCR 접수 경로만 다룬다. Guide·Chat 접수는 동기 one-cycle로 남아 있어 이 수치를 적용할 수
  없다.

## 재현

`backend/app/tests/ocr/` 아래에 임시 계측 테스트를 두고 아래 환경으로 실행한다. `BENCH_PROVIDER_DELAY`로
주입 지연을 바꾸고, 전환 전 수치는 `4038f7ea` worktree에서 같은 방식으로 측정한다.

```bash
env DB_HOST=127.0.0.1 DB_PORT=5432 DB_EXPOSE_PORT=5432 DB_NAME=test \
  STORAGE_DIR="$(mktemp -d)" RELEASE_VALIDATION_ALLOWED=false OCR_STRUCTURE_LLM_ENABLED=false \
  BENCH_PROVIDER_DELAY=0.5 PYTHONPATH="$PWD/backend:$PWD" PYTEST_ADDOPTS= \
  uv run --env-file .env pytest backend/app/tests/ocr/test_intake_latency_bench.py -q -s
```

계측 테스트는 상시 실행 대상이 아니므로 저장소에 두지 않는다. 계측 구간은 접수 호출 한 건이며,
문서 업로드와 로그인은 계측에서 제외한다.
