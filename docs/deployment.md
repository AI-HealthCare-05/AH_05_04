# 배포 가이드

## 환경

- Local
- Staging
- Production

## 배포 절차

인프라가 확정되면 이미지 빌드, 환경변수 설정, DB 마이그레이션, 배포 및 롤백 절차를 기록합니다.

## 동기 AI 배포 기록

OCR·복약 가이드·복약 챗봇은 외부 Provider 호출 중에 요청 단위 DB transaction과 connection을 유지합니다. 같은 채팅 세션의 요청은 row lock에서 추가로 직렬화됩니다. 배포마다 아래 값과 승인 결과를 실제 운영 설정 기준으로 기록합니다.

이 기록은 Issue #38 구현·merge 완료와 분리된 production deployment gate입니다. 대상 환경이 정해지기 전에 값을 추정하거나 승인자를 대신 기입하지 않습니다. 아래 표가 비어 있거나 조건을 충족하지 않으면 구현 검증 통과 여부와 관계없이 해당 환경에 배포할 수 없습니다.

| 항목 | 배포 기록                                                                                                        |
| --- |------------------------------------------------------------------------------------------------------------------|
| 환경·배포 식별자 |                                                                                                                  |
| 확인일·확인자 |                                                                                                                  |
| 실제 OpenAI 모델 | 실제 `OPENAI_MODEL`: ____ (코드 기본값: `gpt-4o-mini`)                                                           |
| OpenAI 전체 timeout `T` | 실제 `OPENAI_TIMEOUT_SECONDS`: ____초 (코드 기본값: 20초)                                                        |
| CLOVA OCR timeout `C` | 실제 `CLOVA_OCR_TIMEOUT_SECONDS`: ____초 (코드 기본값: 20초)                                                     |
| 애플리케이션 처리 여유 `M` | ____초 (기본 참고값: 5초)                                                                                        |
| 동일 세션 최대 동시 전송 `N` | 코드로 강제되는 admission 한도: ____ / 초과 시 응답: ____                                                        |
| Nginx read timeout | 실제 `proxy_read_timeout`: ____초 / 필요 하한 `max(C + E × S + M, N × T + M)`: ____초 / 충족 여부: ____ |
| PostgreSQL lock wait timeout | 실제 `lock_timeout`: ____ (`0`은 제한 없음) / 유한 설정 시 필요 하한 `(N - 1) × T + M`: ____초 / 충족 여부: ____|
| 애플리케이션 replica 수 `R` |                                                                                                                  |
| replica별 Uvicorn worker 수 `W` |                                                                                                                  |
| worker별 in-flight OCR | CLOVA 호출 중인 요청: ____                                                                                       |
| worker별 in-flight 가이드 | OpenAI 호출 중인 요청: ____                                                                                      |
| worker별 in-flight chat | OpenAI 호출 중인 요청과 lock waiter를 모두 포함: ____                                                            |
| worker별 전체 in-flight AI | OCR + 가이드 + chat 합계: ____                                                                                   |
| worker별 비AI 예비 connection | 인증·처방 조회 등을 위해 ____개 예약                                                                             |
| process별 DB pool | 실제 pool size: ____ / overflow: ____ / 총 수용량: ____                                                          |
| PostgreSQL 전체 connection 예산 | 실제 `max_connections`: ____ / 운영 예비: ____ / `R × W × (pool + overflow) + 운영 예비`: ____ / 충족 여부: ____ |
| DB connection wait 정책 | pool wait timeout·queue 정책: ____ / 허용 가능한 대기: ____                                                      |
| 외부 생성 중 DB connection 점유 | tradeoff 승인 여부·승인자: ____                                                                                  |
| 수용량 판정 | `전체 in-flight AI <= pool + overflow - 비AI 예비 connection`: ____                                              |
| 가이드 OpenAI 실호출 | `RUN_OPENAI_SMOKE=1` 실행 환경·일시·결과: ____                                                                   |
| 챗봇 OpenAI 실호출 | `RUN_OPENAI_CHAT_SMOKE=1` 실행 환경·일시·결과: ____                                                              |
| 챗봇 최근 대화 활성화 | 실제 `CHAT_HISTORY_CONTEXT_ENABLED`: ____ (기본값 `false`, Staging·Production은 반드시 `false`) |
| OCR 구조화 LLM 활성화 `E` | 실제 `OCR_STRUCTURE_LLM_ENABLED`: ____ (`false`=0, `true`=1) |
| OCR 구조화 timeout `S` | 실제 `OCR_STRUCTURE_TIMEOUT_SECONDS`: ____초 (코드 기본값: 30초) |

`N`은 문서에 적은 예상값이 아니라 lock 획득 전에 코드가 실제로 강제하는 동일 세션 admission 한도여야 합니다. 현재 구현은 세 개 이상의 요청도 직렬화하며 최대 동시 전송 수를 제한하지 않으므로 유한한 `N`이 없습니다. 따라서 현재 상태에서는 모든 허용 요청을 포괄하는 Nginx·PostgreSQL timeout 하한을 계산할 수 없으며 Production 배포가 차단됩니다.

OCR 요청에서는 CLOVA와 OCR 구조화 OpenAI가 순차 실행되므로 두 Provider timeout을 `C + E × S`로 합산합니다. 기본 참고값 `C=20초`, `S=30초`, `T=20초`, `M=5초`를 적용하면 OCR LLM이 비활성화된 경우 OCR read timeout 하한은 25초이고, 활성화된 경우 55초입니다.
Chat은 동일 세션 최대 동시 전송 `N`이 코드로 강제된 이후 `N × T + M`을 사용합니다. 따라서 Nginx read timeout의 전체 필요 하한은 `max(C + E × S + M, N × T + M)`입니다. `N`이 강제되지 않은 현재 상태에서는 Production 전체 하한을 확정할 수 없으므로 배포 차단 상태를 유지합니다.

다음 조건을 모두 충족해야 배포할 수 있습니다.

- 동일 세션 최대 동시 전송 `N`을 lock 획득 전에 강제하고 초과 요청의 `409` 또는 `429` 계약·테스트를 확정합니다.
- 실제 `proxy_read_timeout >= max(C + E × S + M, N × T + M)`을 확인합니다.
- PostgreSQL `lock_timeout`이 `0`이거나, 유한하게 설정한 경우 `(N - 1) × T + M`보다 큰지 확인합니다.
- worker별 전체 in-flight AI에 OCR·가이드·채팅 외부 호출과 채팅 row lock waiter를 포함하고, pool·overflow 총 수용량에서 비AI 요청용 예비 connection을 먼저 제외해 수용 가능한지 확인합니다.
- 모든 replica와 Uvicorn worker가 process별 pool을 각각 만든다는 기준으로 `R × W × (pool + overflow) + 운영 예비 <= PostgreSQL max_connections`를 확인합니다.
- PostgreSQL `lock_timeout`은 `SHOW lock_timeout`으로 실제 값을 확인하고, 밀리초·초 등 반환 단위를 포함해 기록합니다.
- DB pool size, overflow, pool wait timeout·queue 정책과 허용 가능한 connection 대기시간을 실제 배포 설정으로 기록합니다. 저장소는 `DB_CONNECTION_POOL_MAXSIZE`만 명시적으로 설정하므로 overflow와 wait 정책은 배포 런타임의 실제 값을 확인합니다.
- AI 외부 호출 동안 DB transaction과 connection을 유지하는 현재 설계를 해당 수용량에서 운영할 것인지 명시적으로 승인합니다.
- 가이드와 챗봇의 synthetic live smoke를 실제 배포 모델과 timeout으로 실행하고 결과를 기록합니다. 기본 CI에서 skip된 결과를 실호출 성공으로 간주하지 않습니다.
- Staging·Production의 `CHAT_HISTORY_CONTEXT_ENABLED=false`를 확인합니다. 현재 설정 검증은 Local 이외 환경의 활성화를 거부하며, 실제 사용자 history 전송 승인이 완료될 때까지 `history: []`만 허용합니다.

수용량이 부족하거나 비AI 요청의 connection 대기가 허용 범위를 넘으면 배포하지 않습니다. 세션 잠금을 조용히 약화하는 대신 admission/rate limiting 또는 비동기 worker 설계를 먼저 도입합니다.

현재 두 OpenAI live smoke test는 `OPENAI_MODEL=gpt-4o-mini`만 허용합니다. 실제 배포 모델이 다르면 현재 smoke 결과로 승인할 수 없으며, 해당 모델을 명시적으로 검증하도록 smoke test를 먼저 갱신해야 합니다.

## 의료 AI 안전 승인

`SECURITY.md`는 답변의 처방 버전, 근거, 모델·프롬프트 버전과 검증 결과 추적을 요구합니다. 현재 MVP에는 공식 Identity·Preflight, Rule-first RAG·Citation·Safety, 코드로 강제되는 허용 질문 범위와 AI 응답 품질 평가가 구현되어 있지 않으므로 복약 가이드·챗봇의 Production 배포는 차단됩니다. 승인자와 수동 검토 결과를 Markdown 표에 기록하는 것만으로 이 차단을 해제할 수 없습니다.

조기 사용자 검증이 필요하면 실제 환자·처방 데이터를 사용하지 않는 접근 통제된 내부 staging 데모로 제한합니다. Production을 허용하려면 다음 중 하나를 별도 보안 설계·계약·구현·검증으로 완료해야 합니다.

- `SECURITY.md`의 근거·검증 추적 원칙을 충족하는 공식 Identity·Preflight, Rule-first RAG·Citation·Safety와 재현 가능한 안전 평가를 구현합니다.
- 또는 허용 질문·사용자·데이터 범위, 만료 시각과 금지 답변을 코드로 강제하는 제한 모드를 구현하고, 합의된 데이터셋·지표·임계값, 책임자 승인과 중단 조건을 보안 ADR·계약에 기록합니다.

## OCR·의료문서 배포 기록

| 항목 | 배포 기록 |
| --- | --- |
| 실제 CLOVA endpoint | 비밀값을 제외한 환경·endpoint 식별자: ____ |
| CLOVA timeout | 실제 `CLOVA_OCR_TIMEOUT_SECONDS`: ____초 |
| CLOVA synthetic 실호출 | 실행 일시·합성 fixture·결과: ____ |
| 실제 `STORAGE_DIR` | ____ |
| 영속 volume·object storage | mount 또는 저장소: ____ / `STORAGE_DIR`와 일치 여부: ____ |
| 백업·복구 | 주기·보관 위치·복구 확인 결과: ____ |
| 의료문서 보존·삭제 | 보존 기간·삭제 요청·파기 확인 절차·승인자: ____ |

코드의 기본 `STORAGE_DIR`는 `uploads/medical_documents`이고 Production Compose는 현재 `media_volume`을 `/app/media`에 mount합니다. 운영 `STORAGE_DIR`를 `/app/media` 아래로 명시적으로 설정하지 않으면 업로드 파일이 영속 volume 밖에 저장될 수 있습니다. 실제 경로와 mount가 일치하지 않으면 배포하지 않습니다.

전체 OCR 실패 응답은 현재 사용자에게 직접 입력을 안내하지만 실제 수동 입력 endpoint·화면은 구현되어 있지 않습니다. 오류 안내를 재시도만 제시하도록 수정하거나 수동 입력 흐름을 구현하기 전에는 해당 안내를 사용자에게 배포하지 않습니다.

## 외부 AI 데이터 전송 승인

| 대상 | 실제 전송 데이터 | Provider 저장·학습·보존 정책 확인 | 승인자·확인일 |
| --- | --- | --- | --- |
| CLOVA OCR | 처방전 파일과 OCR 요청 metadata: ____ | ____ | ____ |
| OpenAI 가이드 | 0-based `source_index`와 파생 `guidance_intent`(`FOLLOW_CONFIRMED_TIMING` 또는 `FOLLOW_CONFIRMED_SCHEDULE`)만 전송. 약명·제품 함량·용량·단위·횟수·시점·기간·식별자는 전송 금지 | `store=False` 포함 실제 저장·학습·보존 정책 확인: ____ | ____ |
| OpenAI 챗봇 | 현재 질문과 확정 약물 필드. 최근 완료 대화 최대 3쌍은 별도 승인 후에만 허용하며 현재 Staging·Production에서는 `history: []`: ____ | `store=False` 포함 실제 정책 확인: ____ | ____ |

가이드 `guidance_intent`는 확정 처방의 `timing_text` 존재 여부에서 파생된 의료 metadata이므로 단순 locator가 아니라 외부 전송 승인 대상으로 검토합니다. 승인 범위를 넘는 식별자, 원본 처방값, 최근 대화, OCR 원문·미검수 값이나 내부 오류 metadata가 외부 payload에 포함되면 배포하지 않습니다. Chat history의 버전된 합성 평가, latency와 PII sentinel 검증은 [Issue #129](https://github.com/AI-HealthCare-05/AH_05_04/issues/129)에서 추적하며, 완료되더라도 별도 외부 전송·Production 승인을 자동으로 해제하지 않습니다.

`guide-prompt-v3`는 intent별 승인 guidance와 공통 notice만 선택하도록 제한하며 Backend가 index·intent·exact membership을 검증합니다. 이 제한 생성과 Local 합성 평가 통과는 현재 의료 AI Production 차단을 해제하지 않습니다.

## Production 실행 확인

- Production Compose는 `postgres → migrate → fastapi/ai-worker` 의존 순서를 사용합니다.
- 배포 스크립트는 PostgreSQL과 Redis의 health check 통과를 기다린 뒤 제한된 애플리케이션 DB 계정을 구성합니다.
- 기존 컬럼 rename, `NOT NULL`, FK 추가처럼 구버전 애플리케이션과 호환되지 않는 schema migration은 기존 `fastapi`와 `ai-worker`를 먼저 멈추고 처리 중인 요청이 종료된 뒤 실행합니다. 서비스 중단에 실패하거나 중단 상태를 확인하지 못하면 migration을 실행하지 않습니다.
- schema migration을 실행하는 배포에서는 `fastapi`와 `ai-worker` 이미지를 같은 배포 단위에 포함해 migration 후 새 코드로 재시작합니다. 둘 중 하나만 선택한 부분 배포와 구버전 이미지를 다시 띄우는 배포는 허용하지 않습니다.
- 배포 스크립트는 migration 전 DB backup과 대상 테이블 row count snapshot을 `deployment-evidence/<timestamp>/`에 저장하고, migration 후 SELF profile 수, `profile_id IS NULL`, 부모·자식 `profile_id` 불일치 검증이 통과한 경우에만 애플리케이션 서비스를 재시작합니다.
- PostgreSQL 초기화·Alembic migration 계정과 FastAPI·AI Worker 실행 계정을 분리합니다. FastAPI와 AI Worker에는 테이블 조회·입력·수정·삭제 및 필요한 sequence 사용 권한만 부여하고 schema 객체 생성 권한은 부여하지 않습니다.
- Alembic migration과 migration 후 profile 무결성 검증이 모두 완료된 경우에만 FastAPI와 AI Worker 서비스를 시작합니다.
- Migration이 실패하면 신규 애플리케이션 컨테이너 실행을 중단하고 `migrate` 서비스 로그를 확인합니다.
- Production schema 변경은 forward-fix를 원칙으로 하며 자동 downgrade를 실행하지 않습니다.
- Revision `529b2a36b677`은 `MEDICATION_STRENGTH`, `medication.strength_text`, `ocr_job.prompt_version` 데이터가 하나라도 존재하면 DDL 실행 전에 downgrade를 중단합니다.
- 비운영 환경에서 downgrade가 필요한 경우에만 백업과 영향 확인을 완료하고, 승인된 절차로 신규 필드 데이터를 제거하거나 별도로 보존한 후 실행합니다.
- Production에서 migration 문제가 발생하면 신규 애플리케이션 배포를 중단하고 기존 호환 버전을 유지한 상태에서 후속 migration으로 forward-fix합니다.
- `ai-worker`는 공통 Worker 골격과 단위 테스트가 구현된 상태이며 실제 Redis Consumer 실행 경로는 아직 연결되지 않았습니다. 실제 처리 로직이 연결되기 전에는 비동기 작업 처리 서비스로 운영하지 않지만, schema migration 배포에서는 DB schema 호환성을 위해 FastAPI와 같은 배포 단위에서 새 이미지로 재시작합니다.
- Frontend build·배포 위치와 `VITE_API_BASE_URL`, Nginx routing, HTTPS 및 CORS 실제 값을 함께 확인합니다.

### PostgreSQL volume 초기화 전제

현재 운영 전환 범위는 기존 MySQL 데이터 이관이 없는 빈 PostgreSQL 전환입니다.

새로운 `DB_ADMIN_USER`, `DB_MIGRATION_USER`, `DB_APP_USER` 구성은
Production의 `postgres_data` volume이 아직 초기화되지 않은 fresh volume을
전제로 합니다.

배포 전에 다음 명령으로 기존 volume 존재 여부를 확인합니다.

```bash
docker volume inspect postgres_data
```

- volume이 존재하지 않으면 새로운 Admin·Migration·Runtime 역할로 최초 초기화합니다.
- volume이 존재하지만 보존할 데이터가 없고 삭제 승인을 받은 경우 fresh volume으로 다시 초기화합니다.
- volume에 보존할 데이터가 있거나 PR #72 설정으로 이미 초기화됐다면 바로 배포하지 않습니다.
- 기존 Bootstrap 역할을 사용해 새로운 Admin·Migration 역할을 생성하고 객체 소유권과 권한을 이전하는 일회성 전환 절차를 먼저 수행해야 합니다.
- 기존 volume을 삭제하거나 역할 권한을 변경하기 전에는 Infrastructure 담당자의 확인을 받습니다.

배포 스크립트는 세 역할 이름이 서로 같은 경우 실행을 중단합니다.
- `DB_ADMIN_USER`
- `DB_MIGRATION_USER`
- `DB_APP_USER`

기존 Runtime 역할에 부여된 sequence `UPDATE` 권한은 역할 설정 SQL에서 명시적으로 `REVOKE`한 뒤 `USAGE`, `SELECT`만 다시 부여합니다.

## Post-MVP-1 비동기 전환 게이트 — 미구현

현재 동기 배포 기록은 비동기 전환이 실제로 완료될 때까지 유효합니다. 아래 항목은 승인된 목표이며 현재 배포 경로가 아닙니다.

- OCR·Guide·Chat은 각각 `ASYNC_OCR`, `ASYNC_GUIDE`, `ASYNC_CHAT` feature flag로 전환하고 신규 접수만 선택한 경로로 보냅니다.
- 기존 비동기 Job은 rollback 시에도 drain하며 실행 중간에 동기 경로로 바꾸지 않습니다.
- Redis consumer, dispatch, retry·lease·fencing, Outbox publisher와 reconciler를 구현하고 통합 테스트합니다.
- 초기 내부 SLO는 queue delay p95 5초 이하, terminal 도달 p95 `OCR 60초 / Guide 120초 / Chat 90초`, 15분 이상 non-terminal 0건입니다.
- retry·reclaim·STALE 비율을 계측하고 DLQ·quarantine 발생, Safety 검증 우회와 STALE 결과 공개는 1건부터 경보합니다. 비율 threshold는 초기 2주 계측 후 재승인합니다.
- `PUBLIC_TRACK_C`, `PUBLIC_TRACK_F`는 의료·약학·Privacy·Source 승인과 회귀 증빙 전까지 닫아 둡니다. OTC는 F 게이트를 공유하며 별도 `PUBLIC_TRACK_D`를 만들지 않습니다. MFDS 공식 Identity 활성화도 승인·검증된 Source Snapshot, Single Candidate Gate 회귀와 rollback 훈련 전까지 차단합니다.
- Worker 구현 전 Production Compose의 placeholder `ai-worker`를 실제 처리 서비스처럼 배포하지 않습니다.

전환 PR은 [비동기 Job](./contracts/targets/post-mvp-1/async-job-v1.md), [Outbox·Stream](./contracts/targets/post-mvp-1/outbox-stream-v1.md), [테스트 전략](./testing.md)을 구현·운영 설정과 함께 갱신해야 합니다.

### 공통 Privacy Production gate

정책·승인 인수는 권가빈, 보존·삭제·암호화·로그 통제의 기술 증빙은 송은영, 동의·철회·삭제 UX 증빙은 남한솔이 담당합니다. `EXT-PRIV-001` 승인 전에는 production 보존 job, 미승인 외부 Provider 전송과 공개를 차단합니다. 승인 범위는 terminal Job 90일, publish 완료 Outbox·quarantine·DLQ 30일, Idempotency 7일 기본값, 1MiB 동기 snapshot의 암호화·일반 로그 금지, 미발행 DLQ·연결 quarantine의 TTL 제외, 사용자 삭제·legal hold·키 관리, 목적별 최소 Provider allowlist와 동의·철회 차단 증빙입니다. Track C와 F(OTC 포함)의 정확한 해제 조건은 [외부 승인·공개 게이트](./release-gates/post-mvp-1-external-approvals.md)를 따릅니다.

## 보안 확인

- 비밀정보는 저장소에 커밋하지 않습니다.
- 운영 환경변수와 인증서는 승인된 비밀 저장소에서 관리합니다.
- 로그와 오류 응답에 환자 개인정보가 노출되지 않는지 확인합니다.
- 의료문서·질문·AI 결과의 보존·삭제 및 외부 Provider 전송 정책을 승인하고 기록합니다.
