# Post-MVP-1 계약 추적표

| 항목 | 값 |
| --- | --- |
| 상태 | Approved target — Not implemented |
| 기준 요구사항 | `FinalProject Documents/02_Requirements/요구사항_정의서.xlsx` · 2026-08-24 검증 |
| 완료 원칙 | 구현·OpenAPI·migration·자동 테스트·승인 증빙이 연결되기 전에는 통과로 표시하지 않음 |

리뷰에만 등장하고 현재 승인 요구사항 원본에 없는 `REQ-SYS-008`, `REQ-SYS-009`, `NFR-REL-007`, `NFR-REL-008`, `NFR-LOG-004`, `REQ-OCR-017`은 이 표에 새로 만들지 않는다. 요구사항 원본이 정식 갱신된 뒤 version과 함께 추가한다.

| Track | 승인 요구사항 ID | 목표 계약 | 책임 영역·리뷰 경로 | 예정 검증 위치 | fixture·증빙 | 외부 gate | 구현 상태 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | `REQ-SYS-002`, `REQ-ADM-004`, `REQ-ADM-005` | [Async Job](../contracts/targets/post-mvp-1/async-job-v1.md), [Idempotency](../contracts/targets/post-mvp-1/idempotency-v1.md), [Outbox·Stream](../contracts/targets/post-mvp-1/outbox-stream-v1.md), [Prescription Version](../contracts/targets/post-mvp-1/prescription-version-v1.md) | Backend·Worker·Frontend; `/backend/app/`, `/ai_worker/core/`, `/ai_worker/schemas/`, `/frontend/` | `tests/contract/`, `tests/integration/`, `ai_worker/tests/`, `tests/e2e/` | TBD — 구현 PR에서 승인 manifest·실행 결과 연결 | 공통 `EXT-PRIV-001` | Not implemented · evidence 없음 |
| B | `REQ-SYS-006`, `REQ-HIS-015`, `NFR-PRV-004` | [Check-in](../contracts/targets/post-mvp-1/checkin-v1.md), [Prescription Version](../contracts/targets/post-mvp-1/prescription-version-v1.md) | Backend·Frontend; `/backend/app/`, `/frontend/`, `/docs/contracts/` | `backend/app/tests/`, `tests/contract/`, `tests/integration/`, `tests/e2e/` | TBD — 일정·KST 경계·권한 fixture manifest | 공통 `EXT-PRIV-001` | Not implemented · evidence 없음 |
| C | `REQ-HIS-010`, `REQ-HIS-011`, `REQ-HIS-012`, `REQ-CHT-011` | [Check-in·Barrier](../contracts/targets/post-mvp-1/checkin-v1.md), [Safety Result](../contracts/targets/post-mvp-1/safety-result-v1.md) | Backend·Frontend·규칙/안전; `/backend/app/`, `/frontend/`, `/docs/contracts/` | `backend/app/tests/`, `tests/contract/`, `tests/integration/`, `tests/e2e/` | TBD — 실제형 fixture는 승인 manifest 필요 | `EXT-MED-001`, `EXT-MED-002`, `EXT-PRIV-002`, `EXT-SAFETY-001` | Not implemented · `PUBLIC_TRACK_C=false` |
| D | `REQ-CLN-017`, `REQ-CHT-005` | [Safety Result·OTC](../contracts/targets/post-mvp-1/safety-result-v1.md), [Idempotency](../contracts/targets/post-mvp-1/idempotency-v1.md) | Backend·Frontend·RAG/LLM/Evaluation; `/backend/app/`, `/frontend/`, `/ai_worker/tasks/rag/`, `/evals/` | `backend/app/tests/`, `tests/contract/`, `tests/integration/`, `tests/e2e/`, `evals/` | TBD — source/rule/fixture version manifest | `EXT-PHARM-001`, `EXT-SOURCE-001`, `EXT-PRIV-002`, `EXT-SAFETY-001` | Not implemented · `PUBLIC_TRACK_D=false` |
| E | `NFR-AIS-002`, `NFR-PRV-002` | [OCR 정규화](../contracts/current/ocr-medication-normalization.md), [OCR 구조화](../contracts/current/ocr-medication-structuring.md), [Async Job](../contracts/targets/post-mvp-1/async-job-v1.md) | OCR·Evaluation; `/ai_worker/tasks/ocr/`, `/ai_worker/tests/ocr/`, `/evals/` | `ai_worker/tests/ocr/`, `tests/contract/`, `tests/evals/ocr/` | TBD — 승인 synthetic/de-identified OCR manifest | 공통 `EXT-PRIV-001` | Not implemented · baseline evidence 연결 대기 |
| F | `REQ-CHT-001`, `REQ-CHT-018`, `REQ-CHT-021`, `NFR-AIS-003` | [Async Job](../contracts/targets/post-mvp-1/async-job-v1.md), [Safety Result](../contracts/targets/post-mvp-1/safety-result-v1.md), [Prescription Version](../contracts/targets/post-mvp-1/prescription-version-v1.md) | Backend·Frontend·RAG/LLM/Evaluation; `/backend/app/`, `/frontend/`, `/ai_worker/tasks/rag/`, `/ai_worker/tasks/llm/`, `/evals/` | `tests/contract/`, `tests/integration/`, `tests/e2e/`, `ai_worker/tests/`, `evals/` | TBD — source/citation/safety fixture manifest | `EXT-MED-002`, `EXT-SOURCE-002`, `EXT-PRIV-001`, `EXT-PRIV-002`, `EXT-SAFETY-001` | Not implemented · `PUBLIC_TRACK_F=false` |

GitHub handle과 팀 역할 이름은 저장소에서 명시적으로 연결돼 있지 않으므로 이 문서는 둘을 임의 매핑하지 않는다. 실제 PR은 `AGENTS.md`의 review routing table에 따라 변경 경로의 지정 리뷰어에게 수동으로 리뷰를 요청한다.
