# 목적별 동의 Gate 계약 제안 (`PD-207`)

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed — PR #465 저장 기반 병합 · #510 현재 사용자 목적별 동의 상태 API 구현 · #505 OCR 목적 Gate/API/Worker·Frontend 연결 구현 · 다른 목적의 실행 Gate와 최종 정책 승인 미완료 · Current 아님 |
| Decision | [PD-207 목적별 동의 상태와 Provider 호출 Gate 기준](../../governance/decisions/2026-09-10-consent-gate-207.md) |
| Issue | [#207](https://github.com/AI-HealthCare-05/AH_05_04/issues/207) |
| 작성 | 송은영 (`phina-io`) — Backend·DB·Security |
| 확인 | 권가빈 — Privacy·제품 승인, 김지혜 — Worker/OCR, 정현우 — Guide/Chat·AI/RAG, 남한솔 — Frontend 소비 계약 |
| 공개 상태 | Production 공개 승인 아님 · 실제 사용자/환자 데이터 처리 승인 아님 |

## 1. 목적과 적용 범위

이 문서는 `PD-207`에서 정한 목적별 동의 상태와 Provider 호출 Gate를 공유 계약 형태로 정리한다. Backend, Worker/OCR, Guide/Chat, Notification, Frontend가 같은 의미로 동의 상태와 차단 결과를 해석하기 위한 제안이다.

이 문서는 `proposed/` 계약이며 전체 목적별 Gate의 Current 계약이 아니다. PR #465는 `user_consent` migration/model/repository와 Backend·Worker 공통 fixture의 저장 기반을 병합했다. #510은 현재 사용자 목적별 동의 상태 조회·변경 API를 구현했고, 해당 API의 현재 실행 계약은 `docs/contracts/current/user-account.md`에 기록한다. #505는 OCR 목적의 Backend 동의 API·접수 Gate, Worker 재검사·차단 저장과 Frontend 소비를 구현했다. GUIDE/CHAT/NOTIFICATION 실행 연결, OCR 최종 정책 문구·버전, 담당 리뷰와 Production 공개 승인은 별도로 남아 있다.

## 2. 동의 목적

이번 데모 범위의 목적은 다음 4개다.

| purpose | 의미 | 주요 소비자 |
| --- | --- | --- |
| `OCR` | 외부 OCR 인식과 OCR 결과의 외부 LLM 구조화를 함께 고지할 목적. 최종 문구·policy version과 기존 동의자의 재동의는 #458에서 확정 | Backend OCR 접수, OCR Worker |
| `GUIDE` | 복약 가이드 생성 처리를 허용하는 동의 | Guide Backend/AI 경로 |
| `CHAT` | 챗봇 응답 생성 처리를 허용하는 동의 | Chat Backend/AI 경로 |
| `NOTIFICATION` | 앱 내부 알림 처리와 게시/발송을 허용하는 동의 | Notification Backend, Frontend |

회원가입 시 네 목적을 한 화면에서 받을 수 있지만 저장은 목적별로 분리한다. 미선택은 회원가입을 차단하지 않는다. 목적별 기능 실행 시점에만 해당 목적의 동의를 요구한다.

한 목적의 철회는 다른 목적의 row를 변경하지 않는다.

## 3. 저장 상태와 row 없음 판정

저장 상태는 두 개만 사용한다.

| status | 의미 | Gate 판정 |
| --- | --- | --- |
| `GRANTED` | 해당 목적의 현재 policy version에 사용자가 동의함 | 허용 후보 |
| `WITHDRAWN` | 사용자가 해당 목적의 동의를 철회함 | 차단 |

`user_consent` row가 없으면 미동의로 판단한다. 이번 계약 제안에서는 `NOT_GRANTED`, `POLICY_VERSION_EXPIRED`를 저장 enum으로 추가하지 않는다.

`GRANTED` row라도 `policy_version`이 현재 목적별 policy version과 다르면 허용하지 않는다. 다만 자동 호환 판정, 일괄 재동의, 과거 version 호환성 판단은 이번 제안의 구현 범위에서 제외한다.

현재 사용자 동의 상태 API는 이 기준을 Current 계약으로 구현한다. `PUT /api/v1/users/me/consents/{purpose}`는 요청 `policy_version`이 해당 목적의 현재 policy version과 일치할 때만 저장하고, 불일치하면 `422 VALIDATION_FAILED`와 `POLICY_VERSION_MISMATCH`로 거부한다. `GET`/`PUT` 응답의 `is_granted`도 저장된 row가 `GRANTED`이고 저장 `policy_version`이 `current_policy_version`과 일치할 때만 `true`다.

## 4. `user_consent` 스키마 제안

구현 상태: PR #465에서 아래 최소 저장 기반을 병합했다. 물리 테이블은 `backend/alembic/versions/207b1c2d3e4_create_user_consent.py`, SQLAlchemy 모델은 `backend/app/models/user_consents.py`, repository 판정은 `backend/app/repositories/user_consent_repository.py`, 공통 fixture는 `tests/fixtures/consent/consent_gate_207_cases.json`에 있다. #505에서 OCR 목적의 실제 Gate와 외부 Provider 호출 차단을 연결했으며 다른 목적의 연결은 후속 범위다.

최소 테이블은 다음 필드를 가진다.

| 필드 | 제약/의미 |
| --- | --- |
| `id` | row 식별자 |
| `user_id` | 동의 주체. `User` FK |
| `purpose` | `OCR`, `GUIDE`, `CHAT`, `NOTIFICATION` |
| `status` | `GRANTED`, `WITHDRAWN` |
| `policy_version` | 동의 또는 철회 판정에 사용한 목적별 policy version |
| `granted_at` | `GRANTED` 전환 시각. `GRANTED` 상태에서는 필수 |
| `withdrawn_at` | `WITHDRAWN` 전환 시각. `WITHDRAWN` 상태에서는 필수, `GRANTED` 상태에서는 null |
| `created_at`, `updated_at` | 저장·감사 기준 시각 |

동일 사용자와 동일 목적의 current row는 하나만 존재해야 한다. 구현 PR은 `user_id + purpose` unique 제약 또는 동등한 current-row 불변식을 둔다.

과거 동의 이력을 별도 append-only audit으로 남길지는 이 계약에서 확정하지 않는다. 이력 저장을 도입하는 경우 별도 Decision 또는 계약 갱신이 필요하다.

## 5. 공통 판정표

Backend와 Worker는 런타임 코드를 공유하지 않는다. 대신 같은 Decision Table과 Contract Fixture를 사용해 같은 입력에 같은 판정을 내야 한다. 이 import 금지는 양방향 원칙이다. 현재 저장소는 Worker가 Backend app module을 import하지 않는 경계를 계약 테스트로 강제하고 있으며, Backend가 `ai_worker` 런타임 코드를 import하지 않는 역방향 경계는 #416 조건부 승인 항목과 함께 별도 계약 테스트로 고정한다.

| 입력 조건 | 기대 판정 | Provider 호출 |
| --- | --- | --- |
| row 없음 | 차단 | 0건 |
| `GRANTED` + 현재 policy version | 허용 | 기능별 다음 단계에서만 가능 |
| `WITHDRAWN` | 차단 | 0건 |
| 동의 조회 실패 | 차단 | 0건 |
| 요청 목적과 row 목적 불일치 | 차단 | 0건 |
| 계정 비활성 또는 탈퇴 처리 상태 | 차단 | 0건 |
| parent resource 소유자와 동의 주체 불일치 | 차단 | 0건 |

조회 실패는 fail-closed다. 장애 상황에서 동의 상태를 확인할 수 없으면 Provider 호출을 시작하지 않는다.

## 6. 검사 위치

| 기능 | 접수 전 검사 | 실행 직전 재검사 |
| --- | --- | --- |
| OCR | Backend 접수 전에 `OCR` 동의 확인 | OCR Worker handler에서 CLOVA Provider 호출 직전 재검사 |
| Guide | 현재 동기 경로에서는 Provider 호출 직전 확인. 비동기 접수 연결 후 접수 전 확인 | 비동기 Worker 경로가 연결되면 Provider 호출 직전 재검사 |
| Chat | 현재 동기 경로에서는 Provider 호출 직전 확인. 비동기 접수 연결 후 접수 전 확인 | 비동기 Worker 경로가 연결되면 Provider 호출 직전 재검사 |
| Notification | 실제 게시·발송 경로가 연결된 범위의 직전 확인 | 별도 Worker가 생기면 발송 직전 재검사 |

이 계약은 Guide·Chat `202 + Job` 전환 자체를 구현하지 않는다. 해당 전환 PR에서 같은 Gate를 적용한다.

현재 Guide와 Chat은 Backend 동기 경로에서 LLM Provider를 호출한다. `GUIDE` 목적은 가이드 생성에 필요한 처방·복약 컨텍스트와 검색된 Evidence/Citation 후보를, `CHAT` 목적은 사용자 질문, 대화 맥락, 처방·복약 컨텍스트와 검색된 Evidence/Citation 후보를 Provider 호출 범위로 본다. 원본 OCR 이미지, 불필요한 처방 원문 전체, Provider 원문 응답은 목적별 동의만으로 추가 전송하거나 저장하지 않는다.

## 7. Worker/Stream 메시지 경계

`WorkerMessage`, Outbox, Redis Stream envelope에는 `user_id`, 동의 상태, 건강정보, 복약정보를 추가하지 않는다.

OCR Worker는 `ocr_job.document_id -> medical_document.uploaded_by` 조인으로 동의 주체를 확인한다. Worker는 Backend ORM을 import하지 않고 기존 Worker repository 패턴처럼 raw `table()` 선언 또는 동등한 ORM-독립 조회로 필요한 테이블만 조회한다.

OCR Worker는 CLOVA 호출 직전, 선택적인 LLM 호출 직전 및 결과 저장 전 최신 동의를 재검사한다. 안전한 최소 전송 selector가 확정되기 전에는 LLM을 생략하고 규칙 기반 결과를 사용한다. Provider adapter 내부에 DB hook을 두지 않는다.

## 8. 차단과 상태 매핑

| 상황 | Job 생성 | 공통 Job 상태 | Attempt 상태 | OCR 도메인 상태 | 공개 코드/사유 |
| --- | --- | --- | --- | --- | --- |
| Backend 접수 전 미동의 또는 row 없음 | 생성하지 않음 | 해당 없음 | 해당 없음 | 해당 없음 | `CONSENT_REQUIRED` |
| Backend 접수 전 `WITHDRAWN` | 생성하지 않음 | 해당 없음 | 해당 없음 | 해당 없음 | `CONSENT_REQUIRED` |
| 접수 후 Worker 실행 전 철회 | 이미 생성됨 | `STALE` | `BLOCKED` | `FAILED` | OCR `error_code=CONSENT_WITHDRAWN` |
| 동의 조회 실패 | 접수 전에는 생성하지 않음 | 실행 전이면 fail-closed | 실행 전이면 fail-closed | Provider 호출 없음 | 시점별 차단 계열 |

`AiJobStatus.STALE`와 `AiJobAttemptStatus.BLOCKED`는 기존 enum을 사용한다. 새 Worker FailureCode를 만들지 않는다. `STALE`에는 “접수 당시 유효했던 동의가 실행 전에 철회되어 실행 권한의 현재성을 잃음”을 포함한다.

동의 철회로 인한 `STALE + BLOCKED`는 처방 버전 변경으로 인한 기존 `STALE + BLOCKED`와 반드시 구분한다. #505는 공개 Job DTO를 확장하지 않고 OCR 도메인 `error_code=CONSENT_WITHDRAWN`을 저장한다. Job/Attempt의 일반 failure code와 섞지 않는다.

동의 철회 STALE은 Provider 호출 전 차단이므로 Guide/Chat/RAG 결과를 생성하지 않는다. 이 차단은 RAG 실행 경로에 진입하기 전의 동의 Gate 차단이므로 Job 종결 단위의 `release_decision=STALE` 대상이 아니다. 생성된 결과도 없으므로 결과 단위의 `is_current=false` 판정을 새로 만들지 않는다. 이미 생성된 결과를 현재성 상실로 무효화하는 처방 버전 변경 STALE과 별도 원인으로 기록한다.

OCR에는 `STALE` 도메인 상태가 없으므로 `OcrStatus.FAILED`와 `error_code=CONSENT_WITHDRAWN`을 사용한다. `CONSENT_WITHDRAWN`은 Worker 공통 FailureCode가 아니라 OCR 도메인 실패 사유다.

접수 후 실행 직전 동의 철회로 OCR을 종료할 때는 일반 Worker FailureCode 매핑 경로를 사용하지 않는다. 새 Worker FailureCode를 추가하지 않고, OCR 도메인 전용 종료 전이 또는 writer를 통해 `ocr_job.ocr_status=FAILED`, `ocr_job.error_code=CONSENT_WITHDRAWN`을 저장한다. 이 writer는 Provider 호출 전 차단 경로에서만 사용하며, 기존 Worker FailureCode에서 OCR error_code를 파생하는 매핑과 섞지 않는다.

공통 Job 조회는 `STALE`의 상세 철회 사유를 새 응답 필드로 노출하지 않는다. Frontend는 현재 OCR 동의 상태와 OCR `error_code`를 함께 확인하며, `STALE`만으로 철회를 추정하지 않는다.

## 9. 오류 계약 제안

### `CONSENT_REQUIRED`

필요한 목적의 동의가 없거나 철회되어 Backend 접수 또는 동기 Provider 호출을 시작하지 않는 경우의 공통 오류 코드다.

- OCR 접수 전 차단은 #505에서 `403 CONSENT_REQUIRED`로 구현했다. 다른 목적의 HTTP 계약은 후속 범위다.
- 응답 형식은 공통 오류 envelope `{code, message, details, trace_id}`를 따른다.
- `details[].rejected_value`에는 동의 원문, 환자정보, 처방 원문, Provider 응답을 넣지 않는다.

### `CONSENT_WITHDRAWN`

접수 후 Worker 실행 직전 철회를 감지한 OCR 도메인 실패 사유다.

- Backend 공통 오류 응답으로 직접 반환하지 않는다.
- OCR Job 결과의 안전한 `error_code`로 저장한다.
- 사용자 안내는 “동의가 철회되어 처리가 중단되었습니다. 동의 설정을 확인해 주세요.” 수준으로 제한한다.

## 10. Contract Fixture 최소 케이스

Backend와 Worker는 공통 fixture의 동의 판정 기준을 따른다. #505에서 OCR 목적의 조회·철회 차단과 저장 경합을 검증했으며, 아래 Guide/Chat 등 나머지 목적의 실행 연결은 후속 범위다.

| fixture case | 기대 결과 |
| --- | --- |
| `missing_row` | 차단 |
| `granted_current_policy` | 허용 |
| `withdrawn` | 차단 |
| `lookup_failure` | 차단 |
| `purpose_mismatch` | 차단 |
| `inactive_account` | 차단 |
| `owner_mismatch` | 차단 |
| `withdrawn_after_acceptance` | Provider 호출 0건, Attempt `BLOCKED`, Job `STALE`, 내부 사유 `CONSENT_WITHDRAWN`, OCR `FAILED` |
| `stale_reason_prescription_changed` | 처방 버전 변경 STALE과 동의 철회 STALE이 저장 레벨 내부 사유로 구분됨 |
| `withdrawn_after_acceptance_no_result` | RAG 실행 경로 진입 전 차단으로 `release_decision` 대상이 아니며, Guide/Chat/RAG 결과를 생성하지 않아 결과 단위 `is_current` 판정을 새로 만들지 않음 |

## 11. Frontend 소비 경계

회원가입은 미선택 목적이 있어도 성공할 수 있다. 기능 실행 시 필요한 목적의 동의가 없으면 해당 기능만 차단된다.

기능 화면은 반복 동의창을 계속 띄우는 대신 처리 안내와 동의 내역/설정 이동 경로를 제공한다. #505는 OCR 업로드 전 동의 Gate와 프로필의 동의 상태·철회를 연결했다. 외부 OCR과 LLM 구조화를 함께 명시할 최종 안내 문구·policy version은 확정 전이며, 과거 OCR 전용 동의를 LLM 전송 근거로 자동 확대하지 않는다. 현재는 안전한 최소 전송 selector가 없어 LLM 호출을 생략한다.

## 12. 제외와 승인 게이트

이 계약 제안은 다음을 승인하지 않는다.

- 최종 법무 문구와 외부 Privacy 승인
- 실제 사용자 데이터 처리와 Production 공개
- 운영용 보존·삭제 자동화와 Provider 삭제 연동
- 진행 중 외부 요청의 강제 취소·회수와 정교한 철회 경합 제어
- 자동 policy 호환 판정과 일괄 재동의 기능
- OCR 세부 단계별 별도 동의 목적
- Chat 최근 대화, Citation, RAG Source별 세분 동의 목적
- Guide·Chat 비동기 전환 자체
- Notification 외부 발송 Provider 계약

실제 사용자·환자 데이터를 사용하지 않는다는 전제가 바뀌면 실제 데이터 처리와 외부 전송 범위를 다시 검토한다. 이 계약과 Decision만으로 기존 Production·Provider·Safety gate가 해제되거나 외부 승인이 완료된 것으로 처리하지 않는다.

## 13. Current 승격 조건

이 Proposed 계약은 다음이 같은 구현 PR 또는 명시적으로 연결된 PR 묶음에서 충족된 뒤에만 `current/` 승격을 검토한다. PR #465는 저장 기반을 병합했고, #510은 사용자 동의 상태 조회·변경 API를 `current/user-account.md`의 실행 계약으로 기록했다. #505는 OCR 목적의 Gate/API·Worker·Frontend 연결을 구현했다. 다른 목적의 실행 연결, OCR 최종 정책·최소 전송과 담당 리뷰·공개 승인은 후속 범위다.

- `user_consent` migration/model 구현
- 목적·상태 enum과 DB 제약 구현
- Backend 접수 전 Gate 구현
- Worker 실행 직전 재검사 구현
- Backend/Worker Contract Fixture 테스트
- `CONSENT_REQUIRED` 공통 오류 계약 반영
- OCR `CONSENT_WITHDRAWN` 도메인 실패 계약 반영
- Frontend 소비 계약과 사용자 안내 확인
- 권가빈, 김지혜, 정현우, 남한솔 중 영향 영역 담당자의 필요한 리뷰 승인
