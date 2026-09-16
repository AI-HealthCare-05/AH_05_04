# #633 Guide·Chat 피드백 검증 기록

2026-09-16 Local 합성 검증. 구현: 송은영·권가빈, 단일 책임 리뷰: 정현우.
이 문서는 책임 리뷰 승인·실제 사용자 수집·Production 배포 증거가 아니다.

## 구현과 범위

두 종류의 완료 AI 응답에 rating·선택 의견을 저장하고, 같은 target의 재전송·변경·삭제를 지원한다.
Guide/ChatMessage 부모 row를 잠가 동시 최초 제출을 직렬화하고 SELF profile chain을 검증한다.
원문 의견은 API 응답·Provider·일반 로그에 복제하지 않는다.
의견의 NUL·잘못된 Unicode는 DB에 보내기 전에 공통 422로 거절한다.

최초 생성 후 30일 만료 삭제 명령을 제공한다. 수정은 보존 기간을 연장하지 않는다.
대상 직접 삭제는 FK cascade로 연결하며 현재 없는 회원탈퇴 API 전체를 구현한 것으로 해석하지 않는다.
Backend는 Local만 허용하고 Frontend는 개발 빌드에서만 표시한다.

## 실행

| 검사 | 결과 |
|---|---|
| `backend/app/tests/feedback/` | 16 passed — PostgreSQL 17 / pgvector 0.8.6 전용 컨테이너 |
| 기존 Chat 대화품질·blind A/B 단위·Guide v3 평가 | 35 passed; 같은 실행의 피드백 fixture 오류는 수정 후 위 16건으로 재검증 |
| Ruff check / format | 통과 |
| Mypy Backend·Worker | 통과 |
| Alembic 단일 head | `633a1b2c3d4e` 확인 |
| DB logic / protected write 검사 | 통과 |
| 실제 DB runtime·Source writer 권한 / fresh head | 1 passed; 별도 임시 DB에 전체 migration 적용 후 DML 허용·TRUNCATE/Source writer 접근 거절 |
| Frontend build / lint | 통과; 기존 bundle 크기 경고 유지 |
| Frontend 전체 단위 | 36 files / 628 passed (`VITE_API_BASE_URL=http://localhost:8000`, `--maxWorkers=2`) |
| Guide·Chat 브라우저 | 4 passed; Guide 320·390·412px, Chat 완료 ASSISTANT 연결 |
| 저장소 전체 `scripts/ci/run_test.sh` | migration 235 passed / 4 skipped, 당시 head 통과; Backend 2632 passed / 128 skipped / 1 failed로 exit 1 |
| Worker 환경 수정 후 전체 lane | 3786 passed |
| 후속 Redis 통합 4개 파일 | 29 passed |
| 최신 develop 통합 후 피드백·복약 일정 | 44 passed / 1 skipped / 3 failed; 실패는 복약 일정 파일의 기존 검증 |

첫 DB 실행은 sandbox TCP 제한, 다음 실행은 합성 이메일 길이와 ASGI wrapper·rollback 뒤 fixture identity 문제로
실패했다. 해당 테스트 환경·fixture를 수정한 뒤 16개 테스트를 통과했다.
Frontend 전체 최초 실행은 API 주소 누락, 주소를 지정한 병렬 실행은 기존 Chat 초기화 시각 테스트 1건이 실패했다.
동시 worker 수를 2로 제한한 전체 재실행은 628개 모두 통과했다. 이 문제를 숨기기 위해 기존 테스트를 수정하지 않았다.
첫 전체 Python 실행의 Worker 설정 검사 1건은 임시 환경의 Redis 주소·포트를 host 매핑값으로 덮어쓴 탓에
실패했다. Worker 기본값은 redis:6379로 복원한 전체 lane에서 3786개 통과했다.
실제 통합 연결은 host 포트 override를 사용해 Redis 29개를 별도로 통과했다.

최초 전체 Backend 실패는 `test_old_version_replays_but_new_write_conflicts_and_history_stays`의
처방 버전 충돌 409 기대에 200을 반환한 것이다. `66328a09` develop을 병합한 뒤 같은 검증과
`test_latest_prescription_only_preserves_older_occurrences` 두 parameter가 실패했다.
해당 service·repository·test 파일은 origin/develop과 diff가 없으며 #633에서 수정하지 않았다.
피드백 16개는 같은 재실행에서 통과했다. 전체 runner 성공 또는 통합 coverage 통과로 보고하지 않는다.
신규 #633 migration 부모는 아직 미병합 상태에서 develop의 `206b2c3d4e5f` 뒤로 정렬했다.

실제 migration 테스트는 격리 schema에서 신규 revision을 실행하고 두 테이블의 FK·rating·길이·unique,
대상 cascade, 데이터 보존 downgrade guard와 빈 상태 downgrade/reupgrade를 검증한다.
API 동시성 테스트는 서로 다른 PostgreSQL connection에서 두 최초 POST에 해당하는 service 호출을 실행해
한 번만 생성되고 나머지는 같은 결과를 재현하는지 확인한다.

## 합성 평가 연결

`evals/generation/chat-feedback-gold-v1.json`은 기존 #581 27개 case를 그대로 보존하고
사용자 정정 후 재질문 실패 유형의 새 합성 case 1개를 추가한 **검토용 28-case 버전**이다.
provenance 파일은 `SYNTHETIC_DEMO`, `review_status=PENDING`, reviewer/reviewed_at=null을 명시한다.
실제 사용자 의견에서 추출했거나 정현우가 승인한 것으로 표시하지 않는다.

DB 테스트는 합성 부정 의견을 실제 저장하고 NEGATIVE로 조회한 뒤 해당 case와 연결한다.
올바른 합성 replay 출력은 통과하고, 의도적으로 잘못된 재질문으로 바꾸면 기존 판정기가 거절한다.
자동 원문 export·LLM 변환 또는 운영 검토자 권한을 추가하지 않는다.

[결정론적 실행 artifact](issue-633-feedback-replay.json): 28/28 baseline·history replay 통과,
안전 위반 0. **같은 고정 출력의 판정기 검증이며 prompt 개선 전후나 Provider 생성 품질 측정이 아니다.**
실행 시 현재 runtime prompt는 `chat-prompt-v5`이며 artifact에 실제 version/hash를 기록했다.
기존 v3/v4 blind A/B 설정은 별도 비교용 설정이고 이번 작업에서 실행하지 않았다.
새 28-case를 live canonical allowlist로 승격하거나 기존 prompt를 변경하지 않았다.

재현:

```bash
PYTHONPATH=backend:. uv run --env-file envs/.local.env python -m app.evaluation.chat_history_runner \
  --mode deterministic --dataset evals/generation/chat-feedback-gold-v1.json \
  --output /tmp/feedback-replay.json
```

## 이슈 종료 전 남은 증빙

- 정현우의 신규 합성 Gold 기대·금지 응답 검토와 책임 PR 승인.
- 검토된 실제 부정 피드백의 내부 연결 기록 또는 합성 데모로 인수한다는 명시적 제품 판정.
- 승인된 동일 평가셋으로 이전·후보 prompt 비교 실행과 안전·사람 평가. 현재 `NOT_RUN`.
- 실제 사용자 수집 시 고지·처리 근거·감사 접근 수단·일일 삭제 스케줄·백업 파기 확인.

따라서 현재 PR만으로 #633 전체 완료 조건을 체크하거나 이슈를 자동 종료하지 않는다.
