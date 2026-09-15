# #152 로컬 라이브 검증 OCR 동의 게이트 연결 결정안

- 상태: Proposed / 구현 및 로컬 단위 검증 완료. 최종 승인은 단일 책임 리뷰어(송은영) PR 리뷰 승인으로 완료됨.
- 구현 담당: 정현우 (AI/RAG, Track F)
- 단일 책임 리뷰어: 송은영 (@phina-io, Backend/Security)
- 정책 승인 근거: Issue #458에 남긴 권가빈의 로컬 비식별 합성 데모 한정 승인 댓글 ([#458 정책 검토안](../../contracts/proposed/ocr-consent-notice-policy-review-458.md#152-로컬-합성-실행-한정-승인-2026-09-14-458-결정))
- 영향 계약: [Local Live Provider 호출 증적 계약](../../contracts/current/live-provider-call-evidence.md)

## 1. 배경 및 목적

Issue #152의 canonical local release runner(`local-preflight` 및 `local-live-full`)에 Issue #458에서 검토 및 한정 승인된 OCR 동의 게이트를 연결하여, 외부 OCR Provider 호출 전에 유효한 동의가 등록되어 있는지 검증하고 동의 누락·불일치·실패 시 외부 Provider 호출 없이 fail-closed되도록 보장합니다.

## 2. 주요 결정 사항

1. **적용 범위**:
   - Issue #152의 로컬 환경 검증 runner(`local-preflight`, `local-live-full`)에만 한정 적용합니다.
   - staging, production, 실제 사용자 대상 서비스 또는 공개 릴리스 배포 승인이 아닙니다.

2. **승인 정책 버전**:
   - 이번 로컬 합성 데모 실행 승인 버전은 정확히 `ocr-local-synthetic-demo-2026-09-14-v1`로 한정합니다.
   - 환경변수 `OCR_CONSENT_POLICY_VERSION`에 설정되어야 하며, 빈 값, placeholder, `<...>` 형식은 GUARD 단계에서 거부합니다.

3. **OCR 구조화 LLM 비활성화 경계**:
   - `OCR_STRUCTURE_LLM_ENABLED=false` 경계를 유지합니다.
   - runner 프로세스 환경에는 `OPENAI_API_KEY`와 `CLOVA_OCR_SECRET`이 주입되지 않아야 합니다.

4. **실행 순서**:
   - 사전 GUARD → fixture 준비 → 로그인(`POST /api/v1/auth/login`) → OCR 동의(`POST /api/v1/users/me/consents/OCR`) → 문서 업로드(`POST /api/v1/documents`) → OCR 접수(`POST /api/v1/documents/{id}/ocr-jobs`) → 후속 검증
   - 로그인 성공 직후 문서 업로드 및 OCR 접수 전에 반드시 OCR 동의를 완료해야 합니다.

5. **실패 단계 (`failure_stage`) 추가**:
   - `ALLOWED_FAILURE_STAGES`에 `OCR_CONSENT`를 공식 추가합니다.
   - 동의 API 호출 실패, 정책 버전 불일치, 응답 유효성 검증 실패 시 failure_stage는 `OCR_CONSENT`로 기록되고 외부 호출 없이 즉시 fail-closed 처리됩니다.

6. **DB 검증 및 `llm_processing` 증적 강화**:
   - DB 검증 evidence(`ocr_database`)에 `llm_processing` 필드를 포함합니다.
   - `ocr_structuring_expected=False`인 경우:
     - 반드시 `llm_processing == "NOT_REQUESTED"`이어야 하며 `model_version is None`, `prompt_version is None`이어야 합니다.
     - `SKIPPED_MINIMIZATION`은 기능 활성화 후 최소화 정책에 따른 생략 상태이므로 이번 비활성 실행 증거로 인정하지 않고 `DB_VERIFICATION` 실패로 처리합니다.
   - `ocr_structuring_expected=True`인 경우:
     - 반드시 `llm_processing == "APPLIED"`이어야 하며 `model_version`, `prompt_version`이 모두 존재해야 합니다.
     - `SKIPPED_MINIMIZATION`, `NOT_REQUESTED`, `None`은 활성 실행 증거로 인정하지 않고 `DB_VERIFICATION` 실패로 처리합니다.

## 3. 검토 및 승인 주체 명시

- 본 결정안 및 관련 구현의 최종 승인은 AI 어시스턴트의 자체 승인이 아니며, 단일 책임 리뷰어인 송은영(@phina-io)의 PR 코드 및 문서 리뷰와 공식 승인을 통해 완료됩니다.
