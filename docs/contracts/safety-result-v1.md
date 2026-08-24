# Safety Result 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved target — 2026-08-24 팀 인계 기준 |
| 구현·리뷰 | Not implemented · C·D·F 구현 동기화와 CODEOWNERS·외부 승인 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-c-support-v1.md`, `track-d-otc-v1.md`, `track-f-rag-citation-safety-v1.md` |
| Last verified | 2026-08-24 |

## 공통 원칙

안전 결과는 모델의 자연어만 저장하지 않는다. 입력 처방 버전, 구조화 상태, 검증 결과, 공개 결정, 근거 인용을 함께 저장한다. 근거가 없거나 검증이 실패하면 정상 답변으로 공개하지 않는 fail-closed 규칙을 적용한다.

## Track F 상태 축

| 축 | 값 |
|---|---|
| `response_level` | `ROUTINE`, `URGENT`, `EMERGENCY`, `UNKNOWN` |
| `execution_status` | `SUCCEEDED`, `NO_RESULT`, `TIMED_OUT`, `DEPENDENCY_ERROR`, `VALIDATION_ERROR` |
| `release_decision` | `PASS`, `LIMITED`, `REJECTED`, `STALE` |
| `safety_disposition` | `NORMAL`, `URGENT_ROUTED`, `EMERGENCY_ROUTED`, `BLOCKED_ACTION`, `UNKNOWN_RISK` |

Router와 저장 결과를 혼용하지 않는다. `ROUTINE → NORMAL`, `URGENT → URGENT_ROUTED`, `EMERGENCY → EMERGENCY_ROUTED`, `UNKNOWN → UNKNOWN_RISK`로 매핑하고, 정책 검증이 특정 행동을 차단한 경우에만 `BLOCKED_ACTION`을 저장한다.

정확한 조합은 다음으로 고정한다.

| 상황 | execution | release | safety |
|---|---|---|---|
| 승인된 정상 답변 | `SUCCEEDED` | `PASS` | `NORMAL` |
| 승인된 긴급 안내 | `SUCCEEDED` | `PASS` | `URGENT_ROUTED` |
| 승인된 응급 안내 | `SUCCEEDED` | `PASS` | `EMERGENCY_ROUTED` |
| 확정 처방 사실·고정 한계 안내만 가능 | `SUCCEEDED` | `LIMITED` | `NORMAL` 또는 Router 결과 |
| 근거 없음 또는 근거 충돌 | `NO_RESULT` | `REJECTED` | Router 결과 |
| 생성 시간 초과 | `TIMED_OUT` | `REJECTED` | Router 결과 |
| 의존 서비스 실패 | `DEPENDENCY_ERROR` | `REJECTED` | Router 결과 |
| schema·근거·안전 검증 실패 | `VALIDATION_ERROR` | `REJECTED` | Router 결과 |
| 최신 처방 버전이 아님 | 원래 값 보존 | `STALE` | 원래 값 보존 |

`STALE`이면 `is_current=false`이며 현재 답변으로 노출하지 않는다. `REJECTED` 결과에는 생성된 의료 답변을 노출하지 않고 승인된 고정 fallback만 반환한다. `PASS`, `LIMITED`, `REJECTED`와 fallback이 commit되면 공통 `AI_JOB=COMPLETED`다. timeout·dependency 결과도 fallback을 commit하면 `COMPLETED`이며, fallback조차 저장하지 못한 실행 실패만 `AI_JOB=FAILED`다.

## 저장 모델과 근거

Track F의 `safety_result`는 Chat Job과 처방 version에 1:1로 귀속하며 `response_level`, `execution_status`, `release_decision`, `safety_disposition`, `is_current`, 승인 content 또는 fallback을 저장한다. 모델·prompt·validator·source version과 Citation을 함께 기록하고 상태축의 허용 조합은 DB 제약 또는 서비스 검증으로 강제한다. 정확한 물리 컬럼명은 migration·DTO·계약 테스트를 포함한 구현 PR에서 확정한다. 동기 처리인 Track C·D의 도메인 결과를 `AI_JOB`에 귀속하지 않는다.

`fallback_code`는 `NO_APPROVED_EVIDENCE`, `CONFLICTING_EVIDENCE`, `SAFETY_ROUTED`, `PROVIDER_TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `VALIDATION_FAILED`, `PRESCRIPTION_STALE`, `UNSUPPORTED_REQUEST`로 제한한다. Job 기반 ASSISTANT 응답은 `job_id`, Job 상태와 동일한 `generation_status`, nullable `content`, `prescription_version_id`, `is_current`, 세 결과 상태축, `response_level`, nullable `fallback_code`, `citations[]`를 제공한다. non-terminal·`FAILED`·`STALE`에서는 `content=null`이며 `COMPLETED`에서만 승인 답변 또는 fallback을 노출한다.

`retrieval_run` 하나에는 여러 retrieved chunk와 score·rank를 연결한다. Citation은 result의 개별 claim과 evidence source를 연결하며 `source_type`, `source_id`, `source_version`, `locator`, `claim_key`를 가진다. `(safety_result_id, claim_key, source_type, source_id, source_version, locator)`를 unique로 둔다. 허용 source type은 `PRESCRIPTION`, `KNOWLEDGE_CHUNK`, `SAFETY_POLICY`다. 출처 원문 전체와 검증에서 폐기한 생성 답변은 결과 row나 Redis에 복제하지 않는다.

## Source lifecycle

Source는 owner, license, attribution, checksum, 수집일, 승인자, 승인일, 유효일과 `ACTIVE` 또는 `INACTIVE` 상태를 저장한다. 재색인은 새 `source_version`을 만들고 기존 citation을 바꾸지 않는다. `INACTIVE` source는 신규 retrieval에서 제외하지만 과거 결과의 provenance는 보존한다.

## Track C와 D 매핑

- Track C Support는 승인된 고정 규칙과 문구만 사용한다. 위기·응급 신호는 안내 문구를 임의 생성하지 않고 승인 경로로 보낸다.
- Track D는 제품/성분 식별과 안전성 판정을 분리한다. 사용자가 확정한 제품·성분에 대해 구조화 규칙 엔진을 동기로 실행한다.
- 성분 미확정, 미수록, 근거 충돌과 의존성 실패는 `UNKNOWN`이다. 승인된 범위의 전체 평가가 끝났지만 성립 rule이 없으면 `NO_RULE_MATCH_IN_APPROVED_SCOPE`이며 두 결과 모두 `SAFE`로 간주하지 않는다.
- v1 후보 검색은 허가된 제품명·성분명 기반 구조화 검색만 지원한다. 이미지 OCR, 바코드와 자연어 복수 제품 비교는 제외하며 `OTC_CHECK` Job 유형을 추가하지 않는다.

OTC rule은 `rule_id`, `rule_version`, `rule_type`, 좌·우 ingredient code, `severity`, `source_id`, `source_version`, `effective_at`을 저장한다. `rule_type`은 `CONTRAINDICATION`, `DUPLICATE_INGREDIENT`, `CAUTION`, `severity`는 `BLOCK`, `WARN`, `INFO`로 고정한다. 사용자 공개 `public_outcome`은 `PROFESSIONAL_CONFIRMATION_REQUIRED`, `DUPLICATE_INGREDIENT_FOUND`, `CAUTION_FOUND`, `NO_RULE_MATCH_IN_APPROVED_SCOPE`, `UNKNOWN`만 허용하며 `SAFE`, `DO_NOT_USE`, 복용 중단·용량 변경 지시를 금지한다.

단일 `public_outcome`은 평가 불완전 시 `UNKNOWN`, 평가가 완전할 때 `BLOCK → PROFESSIONAL_CONFIRMATION_REQUIRED`, `WARN → CAUTION_FOUND`, `DUPLICATE_INGREDIENT → DUPLICATE_INGREDIENT_FOUND`, `CAUTION 또는 INFO → CAUTION_FOUND`, 성립 rule 없음 → `NO_RULE_MATCH_IN_APPROVED_SCOPE` 순으로 집계한다. Rule row에는 `public_outcome`을 중복 저장하지 않고 versioned 집계 정책과 평가 snapshot에만 저장한다. `NO_RULE_MATCH_IN_APPROVED_SCOPE`는 승인된 범위에서 rule을 찾지 못했다는 뜻이며 안전 보장이 아니다.

Track D 목표 API는 구조화 검색 `GET /api/v1/otc-products?query=...`와 동기 평가 `POST /api/v1/otc-evaluations`이다. 평가는 사용자가 확정한 `product_id` 또는 `ingredient_id` 중 정확히 하나와 기대 `prescription_version_id`, `Idempotency-Key`를 요구한다. 둘 다 있거나 둘 다 없으면 `422 OTC_TARGET_EXACTLY_ONE_REQUIRED`, active version이 다르면 `409 PRESCRIPTION_VERSION_CONFLICT`다. 응답은 `identification_status`, `evaluation_status`, `prescription_version_id`, `evaluated_at`, `public_outcome`, `message_code`, 전체 `matched_rules[]`, `sources[]`, `cta`를 포함한다. 현재 증상은 입력받거나 평가하지 않는다.

결과에는 평가 당시 rule·source version을 snapshot한다. 성립한 모든 rule을 `BLOCK`, `WARN`, `INFO`, `rule_type`, `rule_id` 순으로 반환한다. 동일 source·rule version의 완전 중복만 제거하며 서로 다른 근거의 성립 rule을 숨기지 않는다.

## 보존

사용자에게 보이는 Safety Result와 citation은 계정·사용자 삭제 정책을 따른다. Retrieval 실행 메타데이터는 90일 보존한다. 의료 원문과 질문·답변을 관측용 메타데이터에 복제하지 않는다. 개인정보 또는 의료 검토가 더 엄격한 조건을 정하면 그 조건을 적용한다.

## 공개 게이트

Track C·D·F는 synthetic fixture로 기술 통합을 검증할 수 있다. 실제 사용자 공개는 승인된 실제형 fixture, 의료·약학 검토, 개인정보 검토, 위험 사례 회귀 테스트가 모두 통과할 때까지 각각 `PUBLIC_TRACK_C=false`, `PUBLIC_TRACK_D=false`, `PUBLIC_TRACK_F=false`로 차단한다. 승인 artifact에는 fixture ID, rule/source version, 기대 결과, 검토 범위, 검토자 역할과 승인 시각을 남긴다.
