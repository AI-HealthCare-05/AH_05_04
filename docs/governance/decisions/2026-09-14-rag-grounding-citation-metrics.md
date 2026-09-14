# Product Decision Candidate: RAG Grounding·Citation Metric 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-160-20260914` |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-14 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation 계약 승인 |
| 추적 Issue | [#160](https://github.com/AI-HealthCare-05/AH_05_04/issues/160) |
| 연결 계약 | [`rag-grounding-citation-metrics-v1.md`](../../contracts/proposed/post-mvp-1/rag-grounding-citation-metrics-v1.md) |

## 후보 결정

RAG-EVAL-005 DEV 구현을 위해 다음 경계를 함께 제안한다.

1. 기존 `rag-eval.case-result@1.0.0`은 유지한다. Claim↔Citation edge와 #180 검증·승인 결과는 별도
   `rag-eval.claim-citation-observation@1.0.0` Evaluation projection으로 보존한다.
2. projection에는 답변·Claim·Source 본문을 저장하지 않고 stable key, version, locator, hash, enum과
   검증 결과만 저장한다.
3. `CITATION_PRECISION`, `CITATION_COVERAGE`, `UNSUPPORTED_CLAIM_RATE`,
   `CRITICAL_UNSUPPORTED_CLAIM_RATE`, `UNCITED_MEDICAL_CLAIM_RATE`를 micro ratio로 계산한다.
4. #180의 selection 전체 decision을 Citation별 결과로 위장하지 않는다. Citation별 edge validation은 같은
   identity·evidence·provenance 규칙을 적용하고 authorization selection receipt를 source provenance로
   exact-map한다.
5. Citation이 올바르려면 edge validation과 Citation authorization을 모두 통과하고 Case Gold의
   Claim–Evidence–locator와 exact-match해야 한다.
6. Gold에 없는 emitted Claim의 criticality는 승인된 immutable criticality judgment만 사용한다.
7. Citation Entailment는 승인된 별도 judgment 계약 전까지 `NOT_EVALUATED/null`이다.

## 현재 계약과의 차이

현재 Case Result의 `actual_claim_ids`와 `actual_citation_evidence_ids`는 두 flat 집합이다. 어느 Citation이
어느 Claim을 지지하는지, source type·version·locator·content hash가 무엇인지, validation과 authorization을
통과했는지 재구성할 수 없다. #180의 persistence-free runtime 객체는 필요한 사실을 소유하지만 Evaluation
Run/Case/Variant와 결속한 immutable artifact가 아니다.

기존 Case Result를 확장하면 Schema Set의 canonical member가 바뀌고 모든 기존 결과와 exporter에 영향을 준다.
따라서 이 후보는 기존 schema를 변경하지 않고 최소 projection 하나만 추가한다.

## 승인 시 DEV 구현 경계

- projection Pydantic schema, canonical JSON Schema export와 registry member
- 신규 member를 포함한 다음 Evaluation Schema Set version과 member manifest hash 정렬
- Run/Case/Dataset/input/answer/variant exact binding과 self-hash 검증
- #180 validated selection과 authorization receipt에서의 순수 projection builder
- 다섯 deterministic metric의 partition·slice·cluster bootstrap 집계
- 구조·binding·receipt mismatch의 `INVALID/null`과 Gold/source 품질 mismatch의 completed failure 분리
- unmatched Claim criticality judgment 부재·불완전 상태 처리
- 합성 DEV fixture와 단위·통합 테스트

외부 Provider 호출, frozen HOLDOUT/SAFETY_REGRESSION 관찰, Runtime adapter 연결, active threshold와 Release
`PASS`는 승인 범위가 아니다.

## 승인 조건

책임 리뷰어가 본 Decision과 연결 계약을 포함한 Pull Request의 최신 HEAD에서 실제 `APPROVED` review를
제출해야 Approved Target으로 전이한다. 승인 뒤 상태 디렉터리와 인덱스를 정렬한다. 승인 전에는 schema,
metric kernel 또는 manifest routing을 구현하지 않는다.
