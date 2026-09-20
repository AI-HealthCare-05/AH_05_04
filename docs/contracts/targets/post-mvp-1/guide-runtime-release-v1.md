# Guide Runtime Release/Fallback Projection v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target delta · Phase A implementation review required — #180 |
| 입력 | `GuideCitationRuntimeOrchestrationOutcome` (#890) |
| 출력 | 내부 `GuideRuntimeReleaseResult` |
| 정본 vocabulary | `safety-result-v2.md` |
| 공개 상태 | Backend DTO·OpenAPI·Frontend 계약 아님 |

## 범위

`guide_runtime_release`는 #890이 보존한 Guide/Card·Claim-Citation·Citation Authorization·Finalization 결과를
Safety Result v2 축으로 결정적으로 사영한다. DB, 네트워크, clock, Backend import 및 I/O가 없는 순수 kernel이다.
Evaluation artifact 승인용 `ai_worker.tasks.evaluation.release_gate`를 Runtime Release Gate로 재사용하지 않는다.

입력은 `GuideCitationRuntimeOrchestrationOutcome` 하나다. caller가 `PASS`, fallback code, evidence status,
Citation 또는 생성 내용을 별도로 주장할 수 없다.

## 사영

| authoritative outcome | execution | evidence | release | current | carrier |
| --- | --- | --- | --- | --- | --- |
| `COMPLETED` + generated Card + `AuthorizedCitationSelection` | `SUCCEEDED` | `SUFFICIENT` | `PASS` | `true` | Card + authorized selection |
| `NO_APPROVED_EVIDENCE` | `NO_RESULT` | `INSUFFICIENT` 또는 Source `STALE` | `REJECTED` | `true` | 승인 fallback |
| `CONFLICTING_EVIDENCE` | `NO_RESULT` | `CONFLICTED` | `REJECTED` | `true` | 승인 fallback |
| `PROVIDER_TIMEOUT` | `TIMED_OUT` | 미사영 | `REJECTED` | `true` | 승인 fallback |
| `DEPENDENCY_UNAVAILABLE` | `DEPENDENCY_ERROR` | 미사영 | `REJECTED` | `true` | 승인 fallback |
| `VALIDATION_FAILED` | `VALIDATION_ERROR` | 미사영 | `REJECTED` | `true` | 승인 fallback |
| `PRESCRIPTION_STALE` | 원 실행 상태 미사영 | 미사영 | `STALE` | `false` | 승인 fallback |
| `EXECUTION_CONTEXT_STALE` | 원 실행 상태 미사영 | 미사영 | `STALE` | `false` | 승인 fallback |
| `UNSUPPORTED_REQUEST` | `SUCCEEDED` | 미사영 | `LIMITED` | `true` | 승인 fallback |

`미사영`은 이 입력 carrier가 해당 값을 증명하지 못해 `None`으로 유지한다는 뜻이다. 특히 Safety Result v2가
STALE에서 보존하도록 요구하는 원 실행 상태를 추정하지 않는다. Source evidence STALE을
`EXECUTION_CONTEXT_STALE`로 변환하지 않는다.

## Fail-closed 불변식

- `AuthorizedCitationSelection` 없는 `PASS`는 없다.
- `DiscardGeneratedContent` 또는 구조 불일치가 있으면 생성 Card와 authorized selection을 결과에 싣지 않는다.
- Citation 내부 reason을 public fallback code로 변환하지 않는다.
- 승인 fallback은 기존 `GuidelineFallbackCode`와 `VerifiedGuidelineFallback`만 전달한다.
- 폐기된 생성 text, Source 원문, 새 Citation excerpt를 복사하거나 보존하지 않는다.
- `STALE`은 항상 `is_current=false`다.

## Shared cross-boundary projection

`GuideRuntimeReleaseResult`는 AI Worker 내부 결과다. Backend와의 유일한 runtime output 경계는 다음과 같다.

```text
GuideRuntimeReleaseResult
  -> ai_worker.tasks.rag.guide_release_projection.project_guide_runtime_release()
  -> rag_runtime.guide_release_projection.GuideRuntimeReleaseProjectionOutcome
```

정본 shared pure package는 `rag_runtime.guide_release_projection`이며 contract version은
`guide-runtime-release-projection-v1`이다. 이 module은 `ai_worker`·Backend·DB·network·clock·provider를
import하지 않는다. Backend는 `GuideRuntimeReleaseProjectionCarrier` 또는
`GuideRuntimeReleaseProjectionUnavailable`만 소비하며 `GuideRuntimeReleaseResult`, `GuidelineCard`,
`AuthorizedCitationSelection`, `ValidatedCitationSelection`, authorization receipt를 직접 읽지 않는다.

### Available carrier

`GuideRuntimeReleaseProjectionCarrier`는 typed `PASS | LIMITED | REJECTED | STALE` decision과
`is_current`을 보존한다.

- PASS는 `is_current=true`, approved structural answer, ordered verified citations, fallback 없음만 허용한다.
  answer는 새 formatter가 아니라 이미 승인된 Card의 ordered claim action text, uncertainty text,
  consultation text를 lossless transport로 가진다.
- LIMITED·REJECTED·STALE는 answer/citation 없이 기존 `VerifiedGuidelineFallback`에서 온 typed
  `GuideRuntimeFallbackCode`와 approved fallback text만 가진다. adapter는 모든 현재
  `GuidelineFallbackCode`를 exhaustive mapping하며 alias·normalization·default를 만들지 않는다.
  STALE는 항상 `is_current=false`다.
- verified citation은 `card_target_ref`, `claim_key`, `evidence_key`, `source_type`,
  `source_snapshot_id`, `source_snapshot_member_id`, `source_code`, `source_version`, `locator`,
  `content_sha256`, `display_order`만 가진다.

### Canonical adapter boundary

`project_guide_runtime_release()`만 #893 내부 타입을 안다. PASS에서 adapter가 새로 확인하는 것은
Card `artifact_ref.content_sha256`와 이미 validated/authorized된
`ValidatedCitationSelection.candidate_set.target.target_ref`가 같은 Card를 가리킨다는 최소
cross-object consistency anchor 하나다. anchor가 맞으면 adapter는
`ValidatedCitationSelection.candidate_set.citations`를 기존 display order 그대로 public-safe carrier로
lossless projection한다.

adapter는 corrupt object를 새 fallback decision으로 재해석하지 않도록 #893의 기존
`GuideRuntimeReleaseResult` invariant만 재사용한다. 이는 release policy나 binding semantics의 새 구현이
아니며, 이미 authoritative한 result의 malformed shape를 unavailable로 닫는 경계 검사다.

adapter는 #794 `run_guide_claim_citation_validation()` / `validate_claim_citations()`가 소유하는
Card ↔ upstream evidence exact binding, #869/#882 `finalize_citations()` /
`AuthorizedCitationSelection`이 소유하는 selection ↔ authorization request ↔ receipt binding을
재구현하거나 재검증하지 않는다. upstream evidence 재조회, authorization 재판정, receipt 재검증,
별도 citation identity 비교, deduplicate, reorder도 하지 않는다.

### Typed unavailable

wrong input, malformed/corrupt/impossible release shape, PASS anchor mismatch, 또는 shared fallback mapping
누락은 `GuideRuntimeReleaseProjectionUnavailable`을 반환한다. unavailable은 contract version 외에
generated text, fallback text, citation, execution/evidence status, internal reason, stopped stage, score,
rank, confidence, raw Source text, discarded content, receipt/artifact detail, provider response, credential,
DB detail, 환자 원문을 담지 않는다. malformed state를 정상 `REJECTED` fallback으로 가장하지 않는다.

### #896 consumer migration

#896은 이 shared carrier의 소비자이며 producer가 아니다. #896 적용 시 Backend-local
`GUIDE_RUNTIME_PROJECTION_CARRIER_VERSION`, `GuideRuntimeReleaseProjectionCarrier`,
`GuideRuntimeCanonicalCitationIdentity`, `GuideRuntimeCitationProjectionCarrier` 정의를 제거하고 shared
type을 import한다. Backend public DTO/OpenAPI, persistence/migration, Guide GET rediscovery는 이 계약 범위가
아니다.

## 후속 경계

이 결과는 현재 Sync Backend application/service의 public projection 입력 후보다. Backend DTO, public Citation의
`title`·`url`·`excerpt`, 저장, API와 Frontend schema는 별도 담당 범위다.

Thin LangGraph는 이 kernel만으로 시작하지 않는다. `rag-runtime-v1.md`의 canonical node에 연결할 실제 callable,
안전한 Sync persistence adapter, 승인된 dependency policy가 모두 준비된 뒤 기존 application service를 호출하는
얇은 adapter로만 조립한다. callable이 없는 canonical node를 pass-through/no-op으로 만들지 않는다.
