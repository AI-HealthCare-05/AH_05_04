# Issue 206 회원탈퇴 삭제·보존 매트릭스 초안

**문서 상태**: 검토용 초안
**구현 담당자**: 송은영
**책임 리뷰어**: 권가빈
**관련 이슈**: #206
**문서 성격**: PM/Privacy 정책 인계 및 구현 준비 매트릭스
**정본 아님**: 이 문서는 `docs/contracts/proposed/account-lifecycle-v1.md`를 변경하지 않습니다.

## 목적

회원탈퇴 요청 접수 이후 실제 개인정보·건강정보 삭제·보존 처리와
`account_deletion_request.status=COMPLETED`, `user.account_status=WITHDRAWN` 전이를
구현하기 전에 PM/Privacy 기준을 먼저 확인합니다.

현재 구현된 범위는 아래까지입니다.

- `account_deletion_request` 저장 기반
- 회원탈퇴 요청 접수 API
- `ACTIVE -> WITHDRAWAL_REQUESTED`
- `is_active=false`
- `token_version + 1`
- 기존 access/refresh token 차단
- `WITHDRAWAL_REQUESTED` 계정의 OCR/Guide/Chat 접수 차단 회귀 테스트

아직 구현하지 않은 범위는 아래입니다.

- 실제 개인정보·건강정보 삭제·보존 처리
- `account_deletion_request.status=IN_PROGRESS/COMPLETED/FAILED` 처리 로직
- `user.account_status=WITHDRAWN` 최종 전이
- `withdrawn_at` 저장
- 삭제 실패 재시도·운영 확인 절차

## 정책 근거

정책 검토 근거는 저장소 외부 최종 법률 검토 문서가 아니라 프로젝트 내부 초안인
`개인정보처리방침 이용약관 초안_26.09.15.md`와 가빈님 1차 답변입니다.

해당 초안에서 이 문서가 참조하는 기준은 다음입니다.

- 회원 정보는 회원 탈퇴 시까지 보유하며, 탈퇴 즉시 또는 법령에 따른 일정 기간 내 파기합니다.
- 처방전 원본 이미지, OCR 추출 결과, AI 생성 가이드, 챗봇 대화 내용, 복약 이행 기록 등 건강정보는 회원 탈퇴 시 또는 최종 이용일로부터 3년 보유 후 파기합니다.
- 감사로그에는 민감정보 원문이나 비밀정보를 포함하지 않으며 별도 보존·삭제 정책에 따라 관리합니다.
- 이용자는 동의 철회와 회원 탈퇴를 요청할 수 있습니다.
- 전자적 파일 형태의 개인정보는 기록을 재생할 수 없는 기술적 방법으로 삭제합니다.

가빈님 1차 답변으로 아래 방향은 이미 받았습니다.

- 재인증, 최종 확인, 즉시 로그인 차단은 기존 합의 범위에서 진행할 수 있습니다.
- 재가입 제한은 두지 않고 같은 이메일 신규 가입을 허용합니다.
- 탈퇴 후 기존 데이터는 복구하지 않습니다.
- 탈퇴 완료 안내는 별도 이메일 없이 앱 내 완료 화면으로 제공합니다.
- 실제 삭제·보존 처리는 보완된 PM/Privacy 정책을 기준으로 맞춥니다.

아래 항목은 아직 가빈님 보완 정책 대기입니다.

- 건강정보의 탈퇴 시 삭제 기준과 보존 기간
- 법정 보존 항목이 우리 서비스에 실제 적용되는 범위
- 감사로그 보존·삭제 기준
- 백업 저장소 삭제·익명화 시점
- legal hold 예외

이 문서는 위 기준을 구현 가능한 데이터 처리 매트릭스로 풀어 쓰기 위한 초안입니다.
승인되면 확정 내용만 `account-lifecycle-v1.md`에 흡수하고, 이 초안 문서는 제거하거나
계약 문서로 흡수됐음을 표시합니다.

## 처리 액션 정의

| 액션 | 의미 | 사용자 재노출 | 후속 구현 요구 |
| --- | --- | --- | --- |
| `DELETE` | row 또는 파일을 물리 삭제합니다. | 불가 | FK 순서, orphan 방지, 삭제 성공 검증 필요 |
| `ANONYMIZE` | row는 남기되 사용자 식별자·자유 텍스트·원문 값을 제거하거나 비식별 값으로 바꿉니다. | 불가 | 재식별 불가 기준과 update 대상 컬럼 명시 필요 |
| `RETAIN_AUDIT_ONLY` | 감사·운영 증빙에 필요한 최소 metadata만 보존합니다. | 불가 | 민감정보 원문·token·Provider 원문 저장 금지 |
| `TTL_KEEP` | 기존 TTL 또는 만료 기준까지 보존 후 삭제합니다. | 불가 | 만료 조건과 lazy cleanup/배치 여부 명시 필요 |
| `BLOCK_ONLY` | 탈퇴 요청 직후 접근만 차단하고 최종 삭제·보존 job에서 별도 처리합니다. | 불가 | `WITHDRAWAL_REQUESTED` 상태에서 모든 보호 API 차단 유지 |
| `PUBLIC_REFERENCE_KEEP` | 사용자 데이터가 아닌 공개 기준 데이터로 유지합니다. | 해당 없음 | 사용자 FK가 없는지 확인 필요 |

이 초안에서 `삭제 또는 비식별화`처럼 선택지가 남은 항목은 PM/Privacy 승인 질문으로 남깁니다.
구현 PR에서는 각 테이블별 최종 액션을 하나로 고정해야 합니다.

## 구현 원칙 초안

| 원칙 | 기준 |
| --- | --- |
| 계정 접근 | 탈퇴 요청 접수 시점부터 `WITHDRAWAL_REQUESTED`, `is_active=false`로 차단합니다. |
| 삭제 완료 의미 | `WITHDRAWN`은 삭제·보존 처리와 필요한 감사 기록 정리가 끝난 최종 상태입니다. |
| 사용자 응답 | 탈퇴 요청 API 성공은 "삭제 요청 접수"를 뜻하며 물리 삭제 완료를 뜻하지 않습니다. |
| 건강정보 | 탈퇴 시 삭제 또는 정책상 보존 대상입니다. 보존 대상은 사용자 화면에서 재노출하지 않습니다. |
| 감사로그 | 민감정보 원문을 포함하지 않는 감사·운영 기록은 보존할 수 있습니다. |
| 실패 처리 | 부분 삭제를 성공처럼 표시하지 않고 `FAILED`와 내부 실패 사유로 남깁니다. |
| 재시도 | `FAILED -> IN_PROGRESS` 재시도는 운영·Backend 내부 경로로만 처리합니다. |
| 외부 공개 | PM/Privacy 승인 전 Production 물리 삭제·보존 job과 `WITHDRAWN` 전이는 실행하지 않습니다. |

## 삭제·보존 매트릭스 초안

### Account/Auth

| 테이블·리소스 | 포함 데이터 | 탈퇴 요청 직후 | 최종 액션 초안 | 승인 질문 |
| --- | --- | --- | --- | --- |
| `user` | 이메일, 이름, 비밀번호 hash, 계정 상태, token version, 선택 개인정보 | `WITHDRAWAL_REQUESTED`, `is_active=false`, `token_version+1` | `ANONYMIZE` + `WITHDRAWN` 전이 | 재가입 제한 없음. 같은 이메일 신규 가입은 허용하되 기존 데이터 복구 없음. 이메일 보존 방식은 감사·운영 목적이 있을 때만 필요 |
| `profile` | SELF profile 소유권 루트 | 보호 API 접근 차단 | `ANONYMIZE` 또는 `DELETE` | 의료 row FK의 root라 삭제 순서와 FK 처리 방식 확정 필요 |
| `refresh_session` | refresh token jti 계보 | 기존 token 차단 | `DELETE` | 탈퇴 요청 transaction에서 즉시 삭제해도 되는지 확인 필요 |
| `password_reset_token` | reset token hash, 만료·사용 시각 | 신규 사용 불가 | `DELETE` 또는 `TTL_KEEP` | 탈퇴 계정의 미사용 token을 즉시 삭제할지 만료까지 둘지 결정 필요 |
| `email_verification_token` | 이메일 인증 token hash | 신규 signup gate와 무관 | `DELETE` 또는 `TTL_KEEP` | 재가입 제한은 두지 않으므로 기존 탈퇴 계정 token이 신규 가입 판단에 쓰이면 안 됨 |
| `user_consent` | 목적별 동의 status/version/timestamps | 보호 API 접근 차단 | `RETAIN_AUDIT_ONLY` | 동의 증빙 보존 기간과 식별자 비식별화 방식 결정 필요 |
| `account_deletion_request` | 탈퇴 요청·처리 상태, retry, 내부 실패 코드 | `PENDING` | `RETAIN_AUDIT_ONLY` | 처리 증빙 보존 기간 결정 필요 |

### Profile/Prescription/OCR

| 테이블·리소스 | 포함 데이터 | 탈퇴 요청 직후 | 최종 액션 초안 | 승인 질문 |
| --- | --- | --- | --- | --- |
| `medical_document` | 업로드 문서 metadata, 원본 파일 참조 | 사용자 접근 차단 | `DELETE` | 실제 파일/object storage 삭제 경로 확인 필요 |
| `ocr_job` | OCR 실행 상태·결과 연결 | 신규 접수 차단 | `DELETE` 또는 `RETAIN_AUDIT_ONLY` | 상태 metadata만 남길지 전체 삭제할지 결정 필요 |
| `extracted_field` | OCR 추출 원문·정규화 후보 | 사용자 접근 차단 | `DELETE` | OCR 원문·미검토 값은 즉시 삭제 대상 후보 |
| `prescription` | 처방 소유권·상태 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 3년 보존 대상이면 비식별화 필요 |
| `prescription_version` | 확정 처방 version·hash·상태 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 확정 처방의 보존 필요 범위 결정 필요 |
| `medication` | 약명·용량·횟수 등 확정 약제 정보 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 건강정보이므로 원문 보존 여부 승인 필요 |
| `prescription_version_medication` | version-member 결속 | 사용자 접근 차단 | parent 처리와 동일 | parent 삭제/비식별화 방식에 종속 |

### Guide/Chat/Feedback

| 테이블·리소스 | 포함 데이터 | 탈퇴 요청 직후 | 최종 액션 초안 | 승인 질문 |
| --- | --- | --- | --- | --- |
| `guide` | AI 복약 가이드 결과 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | AI 생성 결과를 건강정보로 보고 삭제할지 비식별 보존할지 결정 필요 |
| `guide_citation` | Guide 근거 연결 | 사용자 접근 차단 | parent 처리와 동일 | `guide` 삭제 시 같이 삭제 |
| `guide_feedback` | Guide 피드백 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 자유 의견이 있으면 원문 삭제 필요 |
| `chat_session` | Chat 세션 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 세션 metadata만 남길지 결정 필요 |
| `chat_message` | 사용자 질문·Assistant 답변 | 사용자 접근 차단 | `DELETE` | 자유 텍스트는 건강정보·개인정보 포함 가능성이 높음 |
| `chat_citation` | Chat 근거 연결 | 사용자 접근 차단 | parent 처리와 동일 | `chat_message` 삭제 시 같이 삭제 |
| `chat_message_feedback` | Chat 피드백 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 자유 의견 포함 여부 확인 필요 |

### Medication Schedule/Check-in/Track C

| 테이블·리소스 | 포함 데이터 | 탈퇴 요청 직후 | 최종 액션 초안 | 승인 질문 |
| --- | --- | --- | --- | --- |
| `lifestyle_times` | 기상·취침·식사 등 생활 시각 | 사용자 접근 차단 | `DELETE` | 개인정보이며 건강정보와 결합되는 데이터 |
| `medication_schedule` | 복약 일정 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 리포트 보존 필요 시 비식별화 여부 결정 |
| `medication_schedule_time` | 일정 시간 | 사용자 접근 차단 | parent 처리와 동일 | parent 삭제/비식별화 방식에 종속 |
| `medication_occurrence` | 복약 발생 기록 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 복약 이행 기록 3년 보존 적용 여부 결정 필요 |
| `medication_checkin` | 복약 여부 기록 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 리포트 집계에서 제외 필수 |
| `checkin_audit` | Check-in 변경 감사 | 사용자 접근 차단 | `RETAIN_AUDIT_ONLY` | 민감 원문 없이 보존 가능한지 확인 필요 |
| `medication_schedule_audit` | 일정 변경 감사 | 사용자 접근 차단 | `RETAIN_AUDIT_ONLY` | 민감 원문 없이 보존 가능한지 확인 필요 |
| `safety_assessment`, `barrier_response`, `support_action_plan`, `action_plan_followup`, `action_plan_followup_audit` | Track C 상태·지원 계획·후속 확인 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 건강·행동 지원 데이터로 보존 필요 범위 확인 필요 |

### Notification/Push

| 테이블·리소스 | 포함 데이터 | 탈퇴 요청 직후 | 최종 액션 초안 | 승인 질문 |
| --- | --- | --- | --- | --- |
| `push_subscription` | endpoint, p256dh/auth 등 push 구독 정보 | 탈퇴 요청 시 발송 불가 | `DELETE` | endpoint·key 계열은 즉시 삭제 후보 |
| `push_delivery` | push 전송 이력 | 신규 발송 차단 | `RETAIN_AUDIT_ONLY` 또는 `DELETE` | 전송 감사 보존 필요 여부 결정 필요 |
| `notification_record` | 알림 제목·본문·읽음 상태 | 신규 알림 차단 | `DELETE` 또는 `ANONYMIZE` | 본문에 건강정보가 들어갈 수 있어 원문 보존 금지 방향 |

### Async/Idempotency/Runtime Context

| 테이블·리소스 | 포함 데이터 | 탈퇴 요청 직후 | 최종 액션 초안 | 승인 질문 |
| --- | --- | --- | --- | --- |
| `ai_job` | OCR/Guide/Chat 등 job metadata | 신규 접수 차단 | `RETAIN_AUDIT_ONLY` 또는 `DELETE` | 민감 payload가 없다는 전제에서 metadata 보존 가능 |
| `ai_job_attempt` | 재시도·실패 metadata | 신규 실행 차단 | parent 처리와 동일 | 예외 메시지에 민감정보 저장 금지 확인 필요 |
| `outbox_event`, `dlq_outbox_event`, `message_quarantine` | 비동기 발행·실패·격리 기록 | 신규 발행 차단 | `DELETE` 또는 `RETAIN_AUDIT_ONLY` | payload에 원문이 있으면 삭제 대상 |
| `idempotency_record` | 요청 fingerprint·응답 snapshot | 신규 보호 API 접근 차단 | `TTL_KEEP` 또는 `DELETE` | 응답 snapshot 민감정보 포함 여부 확인 필요 |
| `ai_job_intake_context`, `ai_job_execution_context`, `ai_job_execution_identification` | RAG 실행 snapshot provenance | 신규 접수 차단 | `RETAIN_AUDIT_ONLY` 또는 `DELETE` | 환자 질문·처방 원문이 없는 provenance만 보존 가능 |
| `retrieval_run`, `retrieval_signal`, `retrieval_hit` | 사용자별 Retrieval 실행 metadata, 처방 version, execution context, hit/signal | 신규 접수 차단 | `RETAIN_AUDIT_ONLY` 또는 `DELETE` | `retrieval_run.job_id -> ai_job.id -> user.id`와 prescription/context chain을 따라 탈퇴 사용자 범위만 처리해야 함 |

### RAG Public Reference Data

| 테이블·리소스 | 포함 데이터 | 탈퇴 요청 직후 | 최종 액션 초안 | 승인 질문 |
| --- | --- | --- | --- | --- |
| `rag_source*`, `rag_catalog*`, `rag_candidate_index*`, `rag_evidence*`, `rag_runtime*`, `eval_*`, `knowledge_*`, `catalog_*` | 공개 Source, Catalog, Runtime, Evaluation, Evidence 기준 데이터 | 영향 없음 | `PUBLIC_REFERENCE_KEEP` | 사용자 FK와 간접 사용자 실행 chain이 없는 공개 기준 데이터인지 후속 구현에서 재확인 |
| `medication_candidate_search`, `medication_candidate_search_result`, `medication_identification` | 사용자 처방 약제 식별·확정 이력 | 사용자 접근 차단 | `DELETE` 또는 `ANONYMIZE` | 처방·약제 정보와 연결되므로 사용자 데이터로 처리 |

### Source Management/Admin

| 테이블·리소스 | 포함 데이터 | 탈퇴 요청 직후 | 최종 액션 초안 | 승인 질문 |
| --- | --- | --- | --- | --- |
| `source_management_permission`, `source_management_audit` | Source 관리 권한·감사 | 일반 사용자 탈퇴와 무관 | `RETAIN_AUDIT_ONLY` | 운영자 계정 탈퇴 시 별도 정책 필요 |

## 즉시 삭제 후보

아래는 보존 근거가 명확하지 않으면 탈퇴 처리에서 `DELETE`를 기본 후보로 둡니다.

- refresh session과 미사용 인증 token
- Push endpoint와 암호화 key
- 처방전 원본 파일
- OCR 원문·미검토 추출값
- Chat 사용자 질문과 Assistant 답변 원문
- Provider 원문 응답 또는 원문 payload가 저장된 모든 row
- notification 본문 중 건강정보·개인정보가 포함될 수 있는 값

## 제한 보존 후보

아래는 정책상 필요하면 `ANONYMIZE` 또는 `RETAIN_AUDIT_ONLY`로 남길 수 있지만,
사용자 API·Provider·리포트·알림·검색에 다시 사용하면 안 됩니다.

- 삭제 요청 처리 증빙
- 동의 version·동의/철회 시각
- 민감 원문 없는 job terminal metadata
- 민감 원문 없는 audit metadata
- 법정·운영 보존이 승인된 건강정보의 비식별화 결과

## `account_deletion_request` 상태 처리 초안

| 현재 상태 | 다음 상태 | 처리 의미 |
| --- | --- | --- |
| `PENDING` | `IN_PROGRESS` | 삭제·보존 처리 시작 |
| `IN_PROGRESS` | `COMPLETED` | 정책 기준 삭제·보존 처리 완료 |
| `IN_PROGRESS` | `FAILED` | 처리 실패. 계정 접근 차단은 유지 |
| `FAILED` | `IN_PROGRESS` | 운영 재시도 |

`COMPLETED` 처리 시 같은 transaction 또는 정합성이 보장되는 경계에서 아래를 함께 만족해야 합니다.

- `account_deletion_request.status=COMPLETED`
- `account_deletion_request.completed_at` 저장
- `account_deletion_request.last_error_code=NULL`
- `user.account_status=WITHDRAWN`
- `user.withdrawn_at` 저장
- `user.is_active=false` 유지
- 기존 access/refresh token은 계속 `401 INVALID_TOKEN`
- 사용자 보호 API, OCR/Guide/Chat/Notification 접수는 계속 차단
- 보존된 metadata가 사용자-facing 조회, Provider payload, 리포트 집계, 알림 발송에 재사용되지 않음

## 실패 처리 초안

삭제·보존 처리 중 실패하면 아래 기준을 따릅니다.

- `user.account_status`는 `WITHDRAWAL_REQUESTED`를 유지합니다.
- `user.is_active=false`를 유지합니다.
- `account_deletion_request.status=FAILED`로 저장합니다.
- `failed_at`, `retry_count`, `last_error_code`를 운영 확인용으로 저장합니다.
- `last_error_code`에는 이메일, 이름, 전화번호, 처방 원문, OCR 원문, Provider 원문, token, exception 전문을 저장하지 않습니다.
- 실패 상태는 사용자-facing API로 노출하지 않습니다.
- 재시도는 `FAILED -> IN_PROGRESS`로만 시작합니다.

## PM/Privacy 승인 상태와 남은 질문

### 1차 답변으로 반영한 항목

| 항목 | 반영 기준 |
| --- | --- |
| 재인증·최종 확인·즉시 로그인 차단 | 기존 합의 범위에서 진행 가능 |
| 재가입 제한 | 제한 없음. 같은 이메일 신규 계정 생성 허용 |
| 기존 데이터 복구 | 탈퇴 후 기존 데이터는 복구하지 않음 |
| 완료 안내 | 별도 이메일 없이 앱 내 완료 화면 제공 |
| 실제 삭제·보존 처리 | 보완된 PM/Privacy 정책 기준으로 구현 |

### 남은 PM/Privacy 결정 질문

아래 항목은 구현자가 임의로 확정하지 않습니다.

1. `user.email`은 완전 삭제, null 처리, irreversible hash 보존 중 무엇으로 처리할까요?
   - 재가입 제한은 없으므로 hash 보존은 로그인·재가입 차단 목적이 아니라 감사·운영 목적이 있을 때만 필요합니다.
2. 건강정보 3년 보존은 모든 처방·OCR·Guide·Chat·Check-in 원문에 적용하나요, 아니면 법정·운영 subset에만 적용하나요?
3. 처방전 원본 이미지와 OCR 원문은 탈퇴 즉시 물리 삭제해도 되나요?
4. Chat 메시지 원문과 Guide 결과 원문은 탈퇴 즉시 삭제가 맞나요?
5. 동의 이력은 사용자 식별자 비식별화 후 감사 목적으로 보존해도 되나요?
6. Push 전송 이력과 Notification 본문은 삭제 대상인가요, metadata 감사 보존 대상인가요?
7. 감사로그 보존 기간과 접근 권한은 어떻게 둘까요?
8. 백업 저장소 삭제·익명화 시점과 legal hold 예외는 어떻게 둘까요?
9. 법정 보존 항목 중 우리 서비스에 실제 적용되는 항목과 기간은 무엇인가요?
10. 운영자가 `FAILED` deletion request를 확인할 때 필요한 최소 식별자는 무엇인가요?

## 구현 PR 인계 체크리스트

이 문서가 승인되면 후속 구현 PR은 최소 아래를 만족해야 합니다.

- 테이블별 최종 액션이 `DELETE`, `ANONYMIZE`, `RETAIN_AUDIT_ONLY`, `TTL_KEEP`, `PUBLIC_REFERENCE_KEEP` 중 하나로 고정됩니다.
- 삭제·보존 대상별 처리 순서가 FK 관계를 깨지 않도록 정의됩니다.
- 처리 시작 시 `PENDING -> IN_PROGRESS` 전이가 원자적으로 수행됩니다.
- 성공 시 `COMPLETED`와 `WITHDRAWN` 최종 전이가 정합성을 유지합니다.
- 실패 시 `WITHDRAWAL_REQUESTED` 접근 차단을 유지하고 `FAILED`로 남깁니다.
- retry가 같은 request를 중복 처리해도 이미 삭제된 대상에서 실패하지 않도록 idempotent하게 동작합니다.
- 민감정보 원문, token, 비밀번호, Provider 원문, OCR 원문을 오류 응답·로그·`last_error_code`에 남기지 않습니다.
- 보존된 데이터는 사용자 API, Provider payload, 리포트 집계, 알림 발송, 검색·추천 입력으로 재사용되지 않습니다.
- 같은 이메일 신규 가입은 허용하되 기존 데이터와 연결하거나 복구하지 않습니다.
- 탈퇴 완료 안내는 별도 이메일 없이 앱 내 완료 화면으로만 제공합니다.
- Local/Test에서는 합성 fixture로 삭제·보존 처리를 검증합니다.
- Production 공개는 PM/Privacy 승인 전까지 차단합니다.

## 후속 구현 테스트 초안

후속 구현 PR에서는 최소 아래 테스트를 추가합니다.

- `PENDING -> IN_PROGRESS -> COMPLETED` happy path
- `COMPLETED` 시 `user.account_status=WITHDRAWN`, `withdrawn_at` 저장
- 처리 실패 시 `FAILED`, `failed_at`, `retry_count`, safe `last_error_code` 저장
- `FAILED -> IN_PROGRESS` 재시도 성공
- 이미 삭제된 row가 있어도 재시도 idempotent 성공 또는 안전한 skip
- 탈퇴 사용자 기존 token이 계속 `401 INVALID_TOKEN`
- 탈퇴 사용자 OCR/Guide/Chat/Notification 접수 차단 유지
- 삭제 대상 원문 row 또는 파일이 남지 않음
- 보존 metadata에 이메일·처방 원문·OCR 원문·Chat 원문·Provider 원문·token이 남지 않음
- 공개 기준 RAG/Catalog/Source/Evaluation 테이블은 사용자 탈퇴로 삭제되지 않음
- `retrieval_run -> ai_job -> user`, `retrieval_run -> prescription_version`, `retrieval_run -> ai_job_execution_context`, `retrieval_signal/retrieval_hit -> retrieval_run` chain을 따라 탈퇴 사용자별 실행 이력만 처리됨
- `ai_job` 또는 `retrieval_run` 삭제·비식별화 시 CASCADE/RESTRICT 동작이 의도한 사용자 범위와 일치함
- `account_deletion_request`에 사용자의 활성 요청이 중복 생성되지 않음

## 승인 후 문서 처리

PM/Privacy 리뷰에서 이 매트릭스가 승인되면 아래 순서로 정리합니다.

1. 확정된 기준만 `docs/contracts/proposed/account-lifecycle-v1.md`에 반영합니다.
2. 필요한 경우 `docs/governance/decisions/2026-09-02-account-lifecycle-contract.md`에 정책 확정 근거를 추가합니다.
3. 이 초안 문서는 삭제하거나, 계약 문서에 흡수됐다는 상태로 변경합니다.
4. 이후 실제 삭제·보존 구현 PR을 엽니다.
