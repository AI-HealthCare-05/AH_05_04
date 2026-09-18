# #633 Guide·Chat 피드백 검증 기록

2026-09-16 Local 합성 검증. 구현: 송은영·권가빈, 단일 책임 리뷰: 정현우.
이 문서는 실제 사용자 수집·Production 배포 증거가 아니다. PR #638 병합 이후 #730에서 제품/운영 기준을 정리했고, PR #740은 AI/RAG 합성 replay evidence를 정렬한다. prompt 전후 비교·사람 검토가 아직 없어 #633 완료 증거로 사용하지 않는다.

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
| 동시성 fixture 정리 수정 후 피드백·복약 일정 | 47 passed; 두 파일을 같은 프로세스에서 순서대로 검증 |

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
CI run `35057999229`에서도 복약 일정 2개가 실패했다. 원인은 #633 동시성 테스트가 별도 connection으로
commit한 부모 User·Profile·처방 graph를 남긴 것이었다. 수정되지 않은 복약 일정 파일의 실패라는 이유로
별개 문제라고 판단한 초기 결론은 잘못이었다. 테스트 실패 여부와 관계없이 `finally`에서 생성한 전체 graph를
FK 순서대로 지우고, 새 connection으로 User·처방·Guide·Chat이 남지 않았는지 검증하도록 수정했다.
동시 제출도 두 task가 모두 종료한 뒤 정리한다. 복약 일정의 제품 코드·기대값은 바꾸지 않았다.
수정 후 피드백과 복약 일정 파일을 이어서 실행한 47개가 통과했다.
전체 로컬 runner 성공 또는 통합 coverage 통과로 보고하지 않는다.
신규 #633 migration 부모는 아직 미병합 상태에서 develop의 `206b2c3d4e5f` 뒤로 정렬했다.

실제 migration 테스트는 격리 schema에서 신규 revision을 실행하고 두 테이블의 FK·rating·길이·unique,
대상 cascade, 데이터 보존 downgrade guard와 빈 상태 downgrade/reupgrade를 검증한다.
API 동시성 테스트는 서로 다른 PostgreSQL connection에서 두 최초 POST에 해당하는 service 호출을 실행해
한 번만 생성되고 나머지는 같은 결과를 재현하는지 확인한다.

## 합성 평가 연결

`evals/generation/chat-feedback-gold-v1.json`은 현재 v5 30개 case를 그대로 보존하고
사용자 정정 후 재질문 실패 유형의 새 합성 case 1개를 추가한 **검토용 31-case 버전**이다.
provenance 파일은 `SYNTHETIC_DEMO`, `review_status=PENDING_RESPONSIBLE_REVIEW`, `responsible_reviewer=ceohwj`, reviewer/reviewed_at=null을 명시한다.
실제 사용자 의견에서 추출했거나 이미 정현우가 승인한 것으로 표시하지 않는다.

DB 테스트는 합성 부정 의견을 실제 저장하고 NEGATIVE로 조회한 뒤 해당 case와 연결한다.
올바른 합성 replay 출력은 통과하고, 의도적으로 잘못된 재질문으로 바꾸면 기존 판정기가 거절한다.
자동 원문 export·LLM 변환 또는 운영 검토자 권한을 추가하지 않는다.

[결정론적 실행 artifact](issue-633-feedback-replay.json): 31/31 baseline·history replay 통과,
안전 위반 0. **같은 고정 출력의 판정기 검증이며 prompt 개선 전후나 Provider 생성 품질 측정이 아니다.**
실행 시 현재 runtime prompt는 `chat-prompt-v5`이며 artifact에 실제 version/hash를 기록했다.
기존 v3/v4 blind A/B 설정은 별도 비교용 설정이고 이번 작업에서 실행하지 않았다.
새 31-case를 live canonical allowlist로 승격하거나 기존 prompt를 변경하지 않았다.

재현:

```bash
PYTHONPATH=backend:. uv run --env-file envs/.local.env python -m app.evaluation.chat_history_runner \
  --mode deterministic --dataset evals/generation/chat-feedback-gold-v1.json \
  --output /tmp/feedback-replay.json
```

## PR #740 synthetic evidence 정리와 남은 종료 조건

- PR #730 병합으로 Local 합성 demo를 제품/운영 기준의 검토 가능한 증빙으로 사용할 수 있는 범위가 정리됐다. 실제 사용자 feedback 운영 실적과 Production 공개 승인은 별도 공개 gate다.
- 신규 합성 Gold case의 기대 응답·금지 응답은 `evals/generation/chat-feedback-gold-v1.json`과 이 replay artifact에 고정되어 있으며, 실제 comment 원문·대화 원문·약명·수치·사용자 상황을 복사하지 않는다.
- PR #740 실행 source는 `evals/generation/chat-feedback-gold-v1.json`과 `docs/validation/issue-633-feedback-replay.json`이며, 실행 기준 base/head는 PR #740 병합 전 작업트리의 Git commit으로 해석한다. 후속 evidence PR에서는 상대적인 최신 브랜치 표현 대신 실행 source SHA, base SHA, head SHA를 모두 기록한다. PR #740 재실행 결과는 기존 `docs/validation/issue-633-feedback-replay.json`과 byte-for-byte 동일했고, 결과는 31/31 baseline·history replay 통과, safety violation 0, dataset SHA-256 `748c5f420e05c224a0ecc258e1dead71b553195be2ea1d4aac445dda1bba137e`, prompt version `chat-prompt-v5`, prompt SHA-256 `891415d165720f9fbcc8a44dfc0f9fbf8e271a5371c3a0b34e2e366715bada70`이다.
- 이 replay는 `run_mode=DETERMINISTIC_REPLAY`, `historical_prompt_reproduction=false`, `provider_evaluation.status=NOT_RUN`인 현재 `chat-prompt-v5` 단일 prompt 판정기 검증이다. 동일 평가셋 prompt 전후 비교, live Provider A/B, 사람 blind review를 실행하지 않았다.
- #633 후속 evidence는 이 31-case를 Chat prompt 개선용 동일 평가셋으로 사용한다. Track F RAG 전후 비교용 Gold와는 목적을 분리하며, #633에서 RAG Gold를 새로 만들거나 합치지 않는다.
- 후속 evidence PR은 feedback 반영 전 prompt와 현재 prompt를 동일 조건으로 비교하고 baseline/candidate 결과를 각각 생성한다. deterministic replay만으로는 prompt 전후 비교 evidence가 아니다.
- 후속 prompt comparison 입력은 `evals/generation/chat-feedback-gold-prompt-comparison-v1.json`에 고정했다. 이 config는 `chat-feedback-gold-v1` dataset, `chat-prompt-v4` baseline snapshot, #624/#581에서 도입된 `chat-prompt-v5` current snapshot, `gpt-4o`, `max_output_tokens=800`, `timeout_seconds=20`, `temperature=0`, `store=false`를 고정하며 `execution_status=NOT_RUN`이다.
- #632는 temperature=0과 direct Guide/Chat Provider 경로의 live safety/variance evidence로 응답 변동성을 낮춘 근거다. RAG 전후 성능 evidence로 표현하지 않는다.
- 정현우 책임 리뷰는 dataset/prompt version·hash, 통제 실행 조건, case별 기계 판정 결과 또는 failure list, safety regression 결과, 전체 비교 요약 artifact를 기준으로 승인 코멘트를 남긴다. 이 PR 승인만으로 #633을 완료하거나 `guide-chat-feedback-v1`을 Current로 승격하지 않는다.

## PR 책임 리뷰 반영 검증

Service 직접 commit을 제거하고 get_db_session의 요청 transaction으로 통일했다.
Guide·Chat 각각 POST·DELETE 응답 조립에 합성 실패를 주입해 실제 get_db_session이
저장을 rollback하고 삭제한 row를 복구하는 4개 검사를 추가했다.
동시 최초 제출은 두 독립 transaction에서 호출자가 commit하도록 변경했다.
Service 반환 후 세 번째 connection의 부모 row 잠금이 55P03으로 거절되는 것을 확인해
부모 잠금이 outer commit까지 유지됨을 검증한다. 생성 1회·재제출 1회도 유지한다.
피드백 20개와 복약 일정 31개를 같은 프로세스에서 실행해 **51 passed**.
이 검증은 책임 리뷰어의 최종 승인이나 실사용 공개 증거가 아니다.

## v5 회귀 기준 및 오류 코드 리뷰 반영

source를 `chat-v5-short-followup-eval-v1.json` 30-case로 고정했다. 원본 cases JSON 블록을
UTF-8 byte-for-byte 보존하고 v5의 live_gate도 동일하게 유지한 뒤 피드백 합성 case 하나만 추가했다.
source·dataset hash와 31-case 결정론적 replay를 다시 생성했다. 원본 v5 파일은 수정하지 않았다.
기존 대상 교체·구어체 이유·병용 안전 단정 사례를 포함하며, 필수 안내를 포함해도
“같이 먹어도 안전합니다”를 주입하면 history와 safety 판정이 모두 실패하는지 검증한다.
Guide 미완료 3개 상태와 Chat USER·미완료 4개 조합 모두 409와 정확한
`FEEDBACK_TARGET_NOT_READY` code를 확인한다. 피드백·복약 일정 순차 검증은 51 passed다.
계약 Current 이동은 최종 책임 리뷰 승인 전 승격 조건 때문에 보류 중이다.
