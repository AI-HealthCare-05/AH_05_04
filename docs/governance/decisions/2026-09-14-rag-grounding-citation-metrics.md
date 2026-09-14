# Product Decision Candidate: RAG Grounding·Citation Metric 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-160-20260914` |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-14 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 김지혜 (`@Jye-rookie`) — Evaluation·Source provenance·Safety fixture 계약 승인 |
| 추적 Issue | [#160](https://github.com/AI-HealthCare-05/AH_05_04/issues/160) |
| 연결 계약 | [`rag-grounding-citation-metrics-v1.md`](../../contracts/proposed/post-mvp-1/rag-grounding-citation-metrics-v1.md) |

## 후보 결정

RAG-EVAL-005 DEV 구현을 위해 다음 경계를 함께 제안한다.

1. 기존 `rag-eval.case-result@1.0.0`은 유지한다. Claim↔Citation edge와 #180 검증·승인 결과는 별도
   `rag-eval.claim-citation-observation@1.0.0` Evaluation projection으로 보존한다.
2. observation은 `ANSWER_GROUNDING | SAFETY | END_TO_END_RAG`의 동일 Run/Case/input/answer에만 결속하고,
   모든 completed Safety/E2E Case에는 `rag-eval.grounding-signal@1.0.0`을 exact-one으로 둔다.
3. Claim/Citation이 없는 정상 차단은 `NOT_APPLICABLE_NO_CLAIMS` signal로 명시하고, 생성 Claim이 있는데
   observation/signal이 없는 경우나 cross-Case 혼용과 구분한다.
4. projection에는 답변·Claim·Source 본문을 저장하지 않고 stable key, version, locator, hash, enum과
   검증 결과만 저장한다.
5. `CITATION_PRECISION`, `CITATION_COVERAGE`, `UNSUPPORTED_CLAIM_RATE`,
   `CRITICAL_UNSUPPORTED_CLAIM_RATE`, `UNCITED_MEDICAL_CLAIM_RATE`를 micro ratio로 계산한다.
6. #180의 selection 전체 decision을 Citation별 결과로 위장하지 않는다. Citation별 edge validation은 같은
   identity·evidence·provenance 규칙을 적용하고 authorization selection receipt를 source provenance로
   exact-map한다.
7. `VALID_CITATION`은 edge validation accepted, Citation authorization authorized, Gold Claim–Evidence–
   locator와 source version/content hash exact-match가 모두 참인 edge다. Precision, coverage, uncited와
   Medical Claim publishability는 이 동일 predicate만 사용한다.
8. Gold에 없는 emitted Claim의 criticality는 승인된 immutable criticality judgment만 사용한다.
9. Citation Entailment는 승인된 별도 judgment 계약 전까지 `NOT_EVALUATED/null`이다.

승인 검증 예제는 다음을 고정한다. `answer_sha256=null`이고 Claim/Citation이 모두 없는 Safety Case는
observation 없이 세 failure boolean이 false인 `NOT_APPLICABLE_NO_CLAIMS` signal을 가진다. Claim이 하나라도
있는데 observation이 없거나 다른 Case signal을 사용하면 `INVALID/null`이다. accepted·authorized인 Medical
Citation 하나가 Gold locator와 다르면 Precision `0/1`, Coverage `0/1`, Unsupported `1/1`, Critical
Unsupported `0/0`, Uncited `1/1`이고 `SOURCE_BINDING_MISUSE=true`다.

## 현재 계약과의 차이

현재 Case Result의 `actual_claim_ids`와 `actual_citation_evidence_ids`는 두 flat 집합이다. 어느 Citation이
어느 Claim을 지지하는지, source type·version·locator·content hash가 무엇인지, validation과 authorization을
통과했는지 재구성할 수 없다. #180의 persistence-free runtime 객체는 필요한 사실을 소유하지만 Evaluation
Run/Case/Variant와 결속한 immutable artifact가 아니다.

기존 Case Result를 확장하면 Schema Set의 canonical member가 바뀌고 모든 기존 결과와 exporter에 영향을 준다.
따라서 이 후보는 기존 schema를 변경하지 않고 observation과 Safety/E2E same-Case signal projection을
추가한다.

## 승인 시 DEV 구현 경계

- projection Pydantic schema, canonical JSON Schema export와 registry member
- 신규 observation/signal member를 포함한 다음 Evaluation Schema Set version과 member manifest hash 정렬
- Run/Case/Dataset/input/answer/variant exact binding과 self-hash 검증
- #180 validated selection과 authorization receipt에서의 순수 projection builder
- 다섯 deterministic metric의 partition·slice·cluster bootstrap 집계
- 구조·binding·receipt mismatch의 `INVALID/null`과 Gold/source 품질 mismatch의 completed failure 분리
- unmatched Claim criticality judgment 부재·불완전 상태 처리
- Safety/E2E same-Case signal과 no-claims 정상 차단 상태 처리
- 합성 DEV fixture와 단위·통합 테스트

외부 Provider 호출, frozen HOLDOUT/SAFETY_REGRESSION 관찰, Runtime adapter 연결, active threshold와 Release
`PASS`는 승인 범위가 아니다.

## 승인 조건

책임 리뷰어가 본 Decision과 연결 계약을 포함한 Pull Request의 최신 HEAD에서 실제 `APPROVED` review를
제출해야 Approved Target으로 전이한다. 승인 뒤 상태 디렉터리와 인덱스를 정렬한다. 승인 전에는 schema,
metric kernel 또는 manifest routing을 구현하지 않는다.
