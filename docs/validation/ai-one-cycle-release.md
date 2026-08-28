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
