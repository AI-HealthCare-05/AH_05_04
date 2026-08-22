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

| 항목 | 배포 기록 |
| --- | --- |
| 환경·배포 식별자 |  |
| 확인일·확인자 |  |
| 실제 OpenAI 모델 | 실제 `OPENAI_MODEL`: ____ (코드 기본값: `gpt-4o-mini`) |
| OpenAI 전체 timeout `T` | 실제 `OPENAI_TIMEOUT_SECONDS`: ____초 (코드 기본값: 20초) |
| CLOVA OCR timeout `C` | 실제 `CLOVA_OCR_TIMEOUT_SECONDS`: ____초 (코드 기본값: 20초) |
| 애플리케이션 처리 여유 `M` | ____초 (기본 참고값: 5초) |
| 동일 세션 최대 동시 전송 `N` | 코드로 강제되는 admission 한도: ____ / 초과 시 응답: ____ |
| Nginx read timeout | 실제 `proxy_read_timeout`: ____초 / 필요 하한 `max(C + M, N × T + M)`: ____초 / 충족 여부: ____ |
| MySQL lock wait timeout | 실제 `innodb_lock_wait_timeout`: ____초 / 필요 하한 `(N - 1) × T + M`: ____초 / 초과 여부: ____ |
| 애플리케이션 replica 수 `R` |  |
| replica별 Uvicorn worker 수 `W` |  |
| worker별 in-flight OCR | CLOVA 호출 중인 요청: ____ |
| worker별 in-flight 가이드 | OpenAI 호출 중인 요청: ____ |
| worker별 in-flight chat | OpenAI 호출 중인 요청과 lock waiter를 모두 포함: ____ |
| worker별 전체 in-flight AI | OCR + 가이드 + chat 합계: ____ |
| worker별 비AI 예비 connection | 인증·처방 조회 등을 위해 ____개 예약 |
| process별 DB pool | 실제 pool size: ____ / overflow: ____ / 총 수용량: ____ |
| MySQL 전체 connection 예산 | 실제 `max_connections`: ____ / 운영 예비: ____ / `R × W × (pool + overflow) + 운영 예비`: ____ / 충족 여부: ____ |
| DB connection wait 정책 | pool wait timeout·queue 정책: ____ / 허용 가능한 대기: ____ |
| 외부 생성 중 DB connection 점유 | tradeoff 승인 여부·승인자: ____ |
| 수용량 판정 | `전체 in-flight AI <= pool + overflow - 비AI 예비 connection`: ____ |
| 가이드 OpenAI 실호출 | `RUN_OPENAI_SMOKE=1` 실행 환경·일시·결과: ____ |
| 챗봇 OpenAI 실호출 | `RUN_OPENAI_CHAT_SMOKE=1` 실행 환경·일시·결과: ____ |

`N`은 문서에 적은 예상값이 아니라 lock 획득 전에 코드가 실제로 강제하는 동일 세션 admission 한도여야 합니다. 현재 구현은 세 개 이상의 요청도 직렬화하며 최대 동시 전송 수를 제한하지 않으므로 유한한 `N`이 없습니다. 따라서 현재 상태에서는 모든 허용 요청을 포괄하는 Nginx·MySQL timeout 하한을 계산할 수 없으며 Production 배포가 차단됩니다.

admission 한도를 구현한 뒤 기본 참고값 `C=20초`, `T=20초`, `M=5초`, `N=2`를 사용하면 Nginx read timeout은 `max(20 + 5, 2 × 20 + 5) = 45초` 이상, MySQL lock wait timeout은 `(2 - 1) × 20 + 5 = 25초`를 초과해야 합니다. `proxy_read_timeout`은 전체 요청 상한이 아니라 두 연속 read 사이의 무응답 상한이지만, 현재 non-streaming 응답은 Provider 호출 완료 전에 body를 보내지 않으므로 이 기준을 확인합니다.

다음 조건을 모두 충족해야 배포할 수 있습니다.

- 동일 세션 최대 동시 전송 `N`을 lock 획득 전에 강제하고 초과 요청의 `409` 또는 `429` 계약·테스트를 확정합니다.
- 실제 `proxy_read_timeout >= max(C + M, N × T + M)`을 확인합니다.
- 실제 `innodb_lock_wait_timeout > (N - 1) × T + M`을 확인합니다.
- worker별 전체 in-flight AI에 OCR·가이드·채팅 외부 호출과 채팅 row lock waiter를 포함하고, pool·overflow 총 수용량에서 비AI 요청용 예비 connection을 먼저 제외해 수용 가능한지 확인합니다.
- 모든 replica와 Uvicorn worker가 process별 pool을 각각 만든다는 기준으로 `R × W × (pool + overflow) + 운영 예비 <= MySQL max_connections`를 확인합니다.
- DB pool size, overflow, pool wait timeout·queue 정책과 허용 가능한 connection 대기시간을 실제 배포 설정으로 기록합니다. 저장소는 `DB_CONNECTION_POOL_MAXSIZE`만 명시적으로 설정하므로 overflow와 wait 정책은 배포 런타임의 실제 값을 확인합니다.
- AI 외부 호출 동안 DB transaction과 connection을 유지하는 현재 설계를 해당 수용량에서 운영할 것인지 명시적으로 승인합니다.
- 가이드와 챗봇의 synthetic live smoke를 실제 배포 모델과 timeout으로 실행하고 결과를 기록합니다. 기본 CI에서 skip된 결과를 실호출 성공으로 간주하지 않습니다.

수용량이 부족하거나 비AI 요청의 connection 대기가 허용 범위를 넘으면 배포하지 않습니다. 세션 잠금을 조용히 약화하는 대신 admission/rate limiting 또는 비동기 worker 설계를 먼저 도입합니다.

현재 두 OpenAI live smoke test는 `OPENAI_MODEL=gpt-4o-mini`만 허용합니다. 실제 배포 모델이 다르면 현재 smoke 결과로 승인할 수 없으며, 해당 모델을 명시적으로 검증하도록 smoke test를 먼저 갱신해야 합니다.

## 의료 AI 안전 승인

`SECURITY.md`는 답변의 처방 버전, 근거, 모델·프롬프트 버전과 검증 결과 추적을 요구합니다. 현재 MVP에는 RAG·Citation/NLI, 코드로 강제되는 허용 질문 범위와 AI 응답 품질 평가가 구현되어 있지 않으므로 복약 가이드·챗봇의 Production 배포는 차단됩니다. 승인자와 수동 검토 결과를 Markdown 표에 기록하는 것만으로 이 차단을 해제할 수 없습니다.

조기 사용자 검증이 필요하면 실제 환자·처방 데이터를 사용하지 않는 접근 통제된 내부 staging 데모로 제한합니다. Production을 허용하려면 다음 중 하나를 별도 보안 설계·계약·구현·검증으로 완료해야 합니다.

- `SECURITY.md`의 근거·검증 추적 원칙을 충족하는 RAG·Citation/NLI와 재현 가능한 안전 평가를 구현합니다.
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
| OpenAI 가이드 | 확정 처방의 약물 필드: ____ | `store=False` 포함 실제 정책 확인: ____ | ____ |
| OpenAI 챗봇 | 현재 질문과 확정 약물 필드: ____ | `store=False` 포함 실제 정책 확인: ____ | ____ |

승인 범위를 넘는 식별자, 이전 대화, OCR 원문·미검수 값이나 내부 오류 metadata가 외부 payload에 포함되면 배포하지 않습니다.

## Production 실행 확인

- Production DB migration 실행 주체·명령·실패 시 rollback 절차를 기록합니다. 현재 Production Compose에는 별도 `migrate` 서비스가 없습니다.
- `ai-worker`는 실제 작업 로직이 없는 placeholder입니다. Production Compose의 `restart: always` 상태로 배포하면 재시작 루프가 발생할 수 있으므로 Worker 구현 전에는 배포 대상에서 제외하거나 정책을 명시적으로 변경합니다.
- Frontend build·배포 위치와 `VITE_API_BASE_URL`, Nginx routing, HTTPS와 CORS 실제 값을 함께 확인합니다.

## 보안 확인

- 비밀정보는 저장소에 커밋하지 않습니다.
- 운영 환경변수와 인증서는 승인된 비밀 저장소에서 관리합니다.
- 로그와 오류 응답에 환자 개인정보가 노출되지 않는지 확인합니다.
- 의료문서·질문·AI 결과의 보존·삭제 및 외부 Provider 전송 정책을 승인하고 기록합니다.
