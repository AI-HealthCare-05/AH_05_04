# Post-MVP-1 목표 계약

> 상태: Approved Post-MVP-1 targets / 문서별 구현 상태 별도 표시

이 디렉터리의 계약은 승인된 Post-MVP-1 구현 목표입니다. 일부 항목은 구현 PR에서 current 문서로 분리·승격될 수 있으며, 각 문서의 구현 상태 표기를 우선합니다. 구현 상태가 명시되지 않은 항목은 현재 API, DB, Worker 또는 공개 기능으로 해석하지 않습니다. 상태와 승인 원본의 우선순위는 [Post-MVP-1 문서 권위](../../../governance/post-mvp-1-document-authority.md)를 따릅니다.

- [비동기 Job 계약 v1](./async-job-v1.md) — Job 상태 조회 GET과 OCR 접수 POST 구현 완료(#148) · OCR/Guide rediscovery GET은 서비스 로직 구현·라우트 등록 보류 · Guide/Chat 접수 POST와 Reconciler 미구현
- [멱등성 계약 v1](./idempotency-v1.md)
- [Transactional Outbox와 Redis Stream 계약 v1](./outbox-stream-v1.md)
- [처방 버전 계약 v1](./prescription-version-v1.md)
- [Check-in과 Barrier 계약 v1](./checkin-v1.md)
- [OCR 비-RAG LLM 구조화 계약 v1](./ocr-llm-structuring-v1.md)
- [MFDS 공식 의약품 식별·Candidate 계약 v1](./medication-identification-v1.md)
- [Safety Result 계약 v1](./safety-result-v1.md) — Approved v4 이력과 Track C 공통 Safety 기준
- [RAG Source 수집·활성화 계약 v1](./rag-source-ingestion-v1.md)
- [RAG Runtime 계약 v1](./rag-runtime-v1.md)
- [RAG Evaluation·Release Gate 계약 v1](./rag-evaluation-v1.md): Schema Set 1.1 implemented candidate · designated approval pending
- [Safety Result·Citation 계약 v2](./safety-result-v2.md) — Track F에서 v1의 Safety·Citation·STALE·Release Gate 목표를 대체

RAG-00 Target은 외부 Authority Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`을 저장소 경계에 투영한다. 외부 논리 계약 `medication-candidate-identification-v1`은 기존 공식 의약품 식별 Target에 통합하며 중복 Target 파일을 두지 않는다. Safety v1은 Approved v4 이력으로 유지하고 v2는 후속 Target으로 관리한다. 어느 문서도 구현·Current Runtime·공개 완료를 의미하지 않는다.

## Current 승격 조건

목표 계약은 관련 코드·migration·OpenAPI/DTO, 계약·통합 테스트와 실행 증빙이 같은 구현 PR에 포함되고 관련 영역의 지정 리뷰어 승인을 받은 뒤에만 `../../current/`로 이동합니다. 외부 승인이나 공개 flag가 필요한 기능은 이 승격과 별도로 [외부 승인 게이트](../../../release-gates/post-mvp-1-external-approvals.md)를 충족해야 합니다.
