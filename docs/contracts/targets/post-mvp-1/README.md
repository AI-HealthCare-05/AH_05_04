# Post-MVP-1 목표 계약

> 상태: Approved target / Not implemented

이 디렉터리의 계약은 승인된 Post-MVP-1 구현 목표이며 현재 API, DB, Worker 또는 공개 기능으로 해석하지 않습니다. 상태와 승인 원본의 우선순위는 [Post-MVP-1 문서 권위](../../../governance/post-mvp-1-document-authority.md)를 따릅니다.

- [비동기 Job 계약 v1](./async-job-v1.md)
- [멱등성 계약 v1](./idempotency-v1.md)
- [Transactional Outbox와 Redis Stream 계약 v1](./outbox-stream-v1.md)
- [처방 버전 계약 v1](./prescription-version-v1.md)
- [Check-in과 Barrier 계약 v1](./checkin-v1.md)
- [Safety Result 계약 v1](./safety-result-v1.md)

## Current 승격 조건

목표 계약은 관련 코드·migration·OpenAPI/DTO, 계약·통합 테스트와 실행 증빙이 같은 구현 PR에 포함되고 관련 영역의 지정 리뷰어 승인을 받은 뒤에만 `../../current/`로 이동합니다. 외부 승인이나 공개 flag가 필요한 기능은 이 승격과 별도로 [외부 승인 게이트](../../../release-gates/post-mvp-1-external-approvals.md)를 충족해야 합니다.
