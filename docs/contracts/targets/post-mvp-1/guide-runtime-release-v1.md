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

## 후속 경계

이 결과는 현재 Sync Backend application/service의 public projection 입력 후보다. Backend DTO, public Citation의
`title`·`url`·`excerpt`, 저장, API와 Frontend schema는 별도 담당 범위다.

Thin LangGraph는 이 kernel만으로 시작하지 않는다. `rag-runtime-v1.md`의 canonical node에 연결할 실제 callable,
안전한 Sync persistence adapter, 승인된 dependency policy가 모두 준비된 뒤 기존 application service를 호출하는
얇은 adapter로만 조립한다. callable이 없는 canonical node를 pass-through/no-op으로 만들지 않는다.
