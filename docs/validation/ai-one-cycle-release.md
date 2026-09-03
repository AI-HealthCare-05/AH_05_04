# AI One-Cycle Release Validation

이 절차는 Issue #61의 MVP Backend/AI one-cycle을 비식별 합성 데이터로 한 번 검증합니다. 새로운 기능,
Frontend E2E 또는 Production 배포 승인이 아닙니다. `local-live-ai`는 Deferred이며 이 절차에 없습니다.

## 실행 전 확인

- [ ] `AGENTS.md`, `SECURITY.md`, `docs/privacy-safety.md`의 합성 데이터·비밀정보 규칙을 확인했습니다.
- [ ] 결정적 release-validation 테스트와 필수 Backend 회귀 검사가 통과했습니다.
- [ ] operator가 새 UUID run ID를 만들고 실행 메모에 기록했습니다.
- [ ] 질문, Guide, Chat 본문, token, API Key와 DB password를 터미널 로그·JSON·Issue에 남기지 않습니다.
- [ ] local FastAPI는 dependency override 없이 host의 별도 process로 실행합니다.
- [ ] local runner는 실제 TCP로 loopback FastAPI만 호출하며 Provider SDK를 직접 호출하지 않습니다.
- [ ] local FastAPI와 runner의 resolved `STORAGE_DIR`가 같고 runner가 해당 경로를 읽고 쓸 수 있습니다.
- [ ] `CLOVA_OCR_INVOKE_URL`이 credential·fragment 없는 허용된 HTTPS Naver Cloud API Gateway host입니다.
- [ ] local runner 환경에는 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY`가 없고 실제 Key는 FastAPI에만 주입됩니다.
- [ ] runner는 `.env`를 읽지 않으며 `DB_*`, `ENV`, validation 설정과 credential이 없는 CLOVA URL만 별도 주입받습니다.
- [ ] local fixture SHA-256과 manifest의 SHA-256이 일치하고 placeholder가 없습니다.
- [ ] staging은 합의된 HTTPS host, DB identity, commit SHA 또는 image digest의 positive allow gate를 통과합니다.
- [ ] staging image digest는 0이 아닌 lowercase 64자리 hex의 canonical `sha256:<digest>` 형식입니다.
- [ ] staging runner 환경에는 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY`가 없고 실제 Key는 FastAPI에만 주입됩니다.
- [ ] staging one-off는 interactive `/dev/tty`와 실행 권한을 제공합니다.
- [ ] staging의 `RELEASE_VALIDATION_STATE_DIR`가 별도 one-off 사이에 공유되는 private mount이며, `0700`
  directory와 `0600` file의 write-close-read 선행 검사를 통과했습니다.
- [ ] 실제 Provider 호출 비용이 발생하는 local live 실행임을 operator가 확인했습니다.
- [ ] runner의 `OCR_STRUCTURE_LLM_ENABLED`, `CLOVA_OCR_TIMEOUT_SECONDS`, `OCR_STRUCTURE_TIMEOUT_SECONDS`, `OPENAI_TIMEOUT_SECONDS`가 검증 대상 Backend 설정과 일치합니다.
- [ ] `local-live-full` 실행 전 Backend process에도 별도로 `ENV=local`, `RELEASE_VALIDATION_ALLOWED=true`를 주입했습니다. runner의 같은 이름 설정은 Backend 설정을 대신하거나 증명하지 않습니다.
- [ ] runner의 `RELEASE_VALIDATION_ALLOWED`는 raw 문자열 `true` 또는 `1`만 허용하며, 공백/대소문자 변경은 허용하지 않습니다. 이는 Backend의 boolean 설정 파싱 계약을 변경하지 않습니다.
- [ ] Backend stdout Provider log의 접근·발췌·보존 범위와 지정 수동 검토자를 Security·Privacy 책임자가 승인했습니다.
- [ ] one-cycle read timeout은 `max(C + E × S, T) + 5초`로 계산됩니다.

## 고정 명령

Preflight는 실제 CLOVA만 호출해 후보 이미지의 field identity를 검사합니다. OpenAI, PATCH, 처방, Guide와
Chat endpoint는 호출하지 않으며 결과가 `READY`여도 one-cycle PASS 증거가 아닙니다.

```bash
# DB_*, ENV, RELEASE_VALIDATION_ALLOWED, CLOVA_OCR_INVOKE_URL, STORAGE_DIR는
# credential을 출력하지 않는 별도 runner 환경으로 먼저 주입합니다.
env -u CLOVA_OCR_SECRET -u OPENAI_API_KEY \
  PYTHONPATH=backend uv run python -m app.release_validation.ai_one_cycle_smoke \
  --mode local-preflight \
  --run-id <uuid> \
  --base-url http://127.0.0.1:8000/api/v1 \
  --candidate-image /private/tmp/ai-one-cycle-candidate.png \
  --scenario-draft /private/tmp/ai-one-cycle-clova-openai-v1.draft.json
```

고정된 합성 이미지로 실제 CLOVA와 OpenAI 전체 local network 흐름을 실행합니다.

```bash
env -u CLOVA_OCR_SECRET -u OPENAI_API_KEY \
  PYTHONPATH=backend uv run python -m app.release_validation.ai_one_cycle_smoke \
  --mode local-live-full \
  --run-id <uuid> \
  --base-url http://127.0.0.1:8000/api/v1 \
  --scenario backend/app/release_validation/scenarios/ai-one-cycle-clova-openai-v1.json
```

배포된 staging Backend에서 OpenAI one-cycle을 실행합니다. `--commit-sha` 또는
`--image-repo-digest`를 하나 이상 전달합니다.

```bash
env -u CLOVA_OCR_SECRET -u OPENAI_API_KEY \
  PYTHONPATH=backend uv run python -m app.release_validation.ai_one_cycle_smoke \
  --mode staging-live \
  --run-id <uuid> \
  --base-url https://<agreed-staging-host>/api/v1 \
  --scenario backend/app/release_validation/scenarios/ai-one-cycle-v1.json \
  --commit-sha <40-character-commit-sha>
```

응답이 유실되거나 crash가 발생하면 state의 `cleanup_not_before`가 지난 뒤 동일 identity로만 정리를
재시도합니다. cleanup-only는 fixture나 Provider 요청을 만들지 않습니다.

```bash
env -u CLOVA_OCR_SECRET -u OPENAI_API_KEY \
  PYTHONPATH=backend uv run python -m app.release_validation.ai_one_cycle_smoke \
  --mode local-live-full \
  --run-id <uuid> \
  --base-url http://127.0.0.1:8000/api/v1 \
  --cleanup-only
```

## 판정과 종료 코드

- exit `0`: 일반 실행의 execution·Guide/Chat 안전 판정·cleanup이 모두 PASS이거나 cleanup-only PASS
- exit `1`: 실행·DB·안전 판정 실패, cleanup PASS
- exit `2`: 첫 변경 요청 전 CLI·환경·scenario guard 실패
- exit `3`: cleanup FAIL 또는 PENDING; 다른 실행 실패보다 우선

stdout에는 JSON 한 건만 허용합니다. `cleanup=PENDING`이나 live test skip은 PASS 증거가 아닙니다. local의
dirty worktree 결과는 진단에는 사용할 수 있지만 `evidence_qualified=false`이며 Issue 완료 증거가 아닙니다.

`local-live-full` 결과의 `execution=PASS`, `database_verification=PASS`, `cleanup=PASS`는 API·DB·정리 검증 성공만 뜻합니다. `provider_log_verification=MANUAL_REQUIRED`인 동안 실제 Provider 호출 증빙은 완료되지 않았으며 자동으로 `PASS`로 바꾸지 않습니다.

실패 결과도 Live 실행 경계에 진입했다면 `execution_mode=LIVE`를 유지합니다. `database_verification`은 DB 검증 전 실패면 `NOT_RUN`, 검증 실패면 `FAIL`, 검증을 통과한 뒤 safety·cleanup에서 실패하면 `PASS`입니다. `provider_log_verification`은 수동 검토 가능한 trace가 있으면 `MANUAL_REQUIRED`, 없으면 `UNVERIFIED`이며 어느 경우에도 runner가 `PASS`를 기록하지 않습니다.

`failure_evidence.api_reason`은 `DEADLINE_EXCEEDED` 또는 `PROVIDER_TIMEOUT`일 때만 존재합니다. 전자는 애플리케이션 전체 예산 소진, 후자는 Provider transport timeout을 뜻합니다. 임의의 API `details` 값은 증빙에 복사하지 않습니다.

## Issue #152 Local Provider 로그 증빙

이 절차는 `local-live-full`에만 적용합니다. staging·production Live 검증이나 배포 설정을 변경하지 않습니다. runner는 모든 Backend 요청에 동일 `X-Validation-Run-Id`를 보내고 응답별 `X-Trace-Id`를 수집합니다. 로그인 후 Authorization을 추가해도 validation Header를 유지합니다.

Backend process는 Provider Secret을 승인된 방식으로 주입받지만 runner process에는 `CLOVA_OCR_SECRET`, `OPENAI_API_KEY`가 없어야 합니다. `RELEASE_VALIDATION_ALLOWED`도 두 process에 각각 주입합니다. local Compose의 `fastapi`는 `envs/.local.env`를 읽으므로 Backend 쪽 값은 그 파일 또는 동등한 Backend 전용 실행 환경에 설정하고, runner는 credential 없는 별도 환경을 사용합니다.

실행 직후 결과의 `run_id`로 Docker Desktop의 `fastapi` Logs를 검색합니다. 빠른 조회는 다음 명령을 사용할 수 있습니다.

```bash
docker compose logs --no-color --no-log-prefix --since 10m fastapi \
  | rg '"validation_run_id":"<run-id>"'
```

정본 JSONL은 Compose prefix나 Desktop 변환이 없는 컨테이너 stdout 원문에서 접근 제한 위치로 발췌합니다.

```bash
docker logs --since 10m fastapi 2>&1 \
  | rg '"validation_run_id":"<run-id>"' \
  > /private/tmp/provider-call-log-<run-id>.jsonl
chmod 600 /private/tmp/provider-call-log-<run-id>.jsonl
```

각 줄을 독립 JSON으로 파싱해 `provider-call-log-v1`, 필수 operation, 동일 trace, started 1건·terminal 최대 1건, 금지정보 부재를 확인합니다. OCR 구조화가 꺼져 있으면 `OCR_STRUCTURING` 로그와 DB model/prompt가 모두 없어야 합니다. 켜져 있으면 로그와 두 DB 필드가 모두 있어야 합니다.

증빙 Artifact는 `one-cycle-result.json`, `provider-call-log-<run-id>.jsonl`, `provider-log-review-<run-id>.json` 세 개이며 모두 같은 `run_id`를 사용합니다. 저장소에 commit하지 않고 승인된 접근 제한 위치에서 팀 보존 정책에 따라 삭제합니다. runner·DB·cleanup·지정 검토자의 수동 Provider 로그 판정이 모두 `PASS`일 때만 전체 증빙을 완료합니다.

Issue #211에서 재사용하는 run `2d8d3356-d019-430f-a31b-34d5c2afaf71`은 추가 Live 호출 없이 처리합니다. `/private/tmp` Artifact는 승인된 접근 제한 위치가 정해진 뒤 이동하고 기존 SHA-256과 대조합니다. 지정된 사람 검토자만 `provider-log-review-v1` 판정 Artifact를 작성할 수 있으며, 승인 위치와 검토자가 없으면 이 단계는 미완료로 남깁니다.

## Issue #61 비민감 결과 양식

```text
실행 시각:
환경 / run ID:
mode / transport:
evidence scope: diagnostic | release
scenario version / input fingerprint:
fixture ID / fixture SHA-256 (local-live-full only):
commit SHA 또는 image digest:
local worktree dirty / evidence qualified:
OCR status / field count / error code (local-live-full only):
Guide status / model ID / prompt version:
Chat status / model ID / prompt version:
새 DB session 재조회: PASS | FAIL
Guide safety / Chat safety / overall:
failed safety codes:
cleanup: PASS | FAIL | PENDING
Backend/AI one-cycle: PASS | FAIL | BLOCKED
실제 Provider 호출: CLOVA yes|no / OpenAI yes|no
비용 발생: yes|no
비고: Production 배포 승인 아님
```

생성 본문, 질문 전문, token, credential, DB password, OCR 원문과 preflight 전문은 기록하지 않습니다.
