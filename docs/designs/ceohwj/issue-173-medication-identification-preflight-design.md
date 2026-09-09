# Issue #173 RAG-12 Medication Identification Preflight 판정 단위 설계

## 상태

- Issue: `#173`
- 브랜치: `feat/173-rag-identification-preflight`
- 기준 commit: `083f69eb` (`origin/develop`)
- 범위: side-effect-free Identification Preflight 판정 kernel과 결정 계약 테스트
- 구현 담당자: 정현우 (`@ceohwj`)
- 담당 리뷰어: 송은영 (`@phina-io`) — RAG-12-API(`#174`) 소비 계약
- Safety 리뷰: 권가빈 (`@hazelnutflavoured`)
- 공개 게이트: `PUBLIC_TRACK_F_ENABLED=false` 유지. 이 slice는 게이트를 건드리지 않는다.

## 정본

이 설계는 저장소의 Approved Target만 따른다.

- `docs/contracts/targets/post-mvp-1/rag-runtime-v1.md` — "입력과 접수 Preflight", "고정 실행 Graph"의
  `medication_identification_preflight` 분기
- `docs/contracts/targets/post-mvp-1/medication-identification-v1.md` — "Guide·Chat Preflight 경계",
  Candidate Search 상태·Identification 불변식
- `docs/contracts/targets/post-mvp-1/prescription-version-v1.md` — "활성화"의 현재성 판정 규칙
- `docs/contracts/proposed/guide-chat-session-message-status-ui-v1.md` — `REVIEW_REQUIRED`가 Job 상태가
  아니라는 경계 (proposed. 이 slice의 판정 값을 여기서 끌어오지 않는다)

## 착수 가능성 판단

### 확보된 선행 계약

| 선행 | 상태 | 이 slice가 소비하는 형태 |
| --- | --- | --- |
| RAG-09 (`#171`) Identification Finalizer | develop에 `backend/app/models/rag_candidate.py`, `backend/app/services/medication_identification.py` 병합 | `MedicationIdentificationStatus`, `MedicationCandidateSearchStatus` enum과 `MATCHED` payload 불변식 |
| RAG-10 (`#172`) API 상태 계약 | DTO(`backend/app/dtos/medication_candidates.py`)와 route 골격은 병합. handler는 `503` stub | 공개 상태 enum 어휘만 참조. endpoint는 호출하지 않는다 |
| RAG-11 (`#131`) 확인 UI | 미착수 | 소비하지 않는다. 판정 결과 code만 노출한다 |
| PRESCRIPTION-VERSION-READY (`#169`) | PR 1·2·4 병합 (`083f69eb`) | `prescription.active_version_id` 일치가 곧 현재성이라는 확정 규칙 |

### 미확정 계약을 추정하지 않기 위한 입력 경계

`rag-runtime-v1.md`의 고정 Graph는 preflight 분기를 다음으로 고정한다.

```text
medication_identification_preflight
   ├─ REVIEW_REQUIRED | AMBIGUOUS | UNAVAILABLE | NOT_FOUND | INVALID_INPUT | UNRESOLVED
   │    → approved_identification_fallback
   ├─ EXECUTION_CONTEXT_STALE
   │    → approved_stale_fallback
   └─ MATCHED
        → pin_full_execution_context
```

반면 저장소의 어떤 정본도 `(candidate_search.status, medication_identification.status)` →
위 7개 값의 사영 규칙을 고정하지 않았다. DB 상태축은 `MATCHED | UNRESOLVED` 2개뿐이고,
`AMBIGUOUS | NOT_FOUND | UNAVAILABLE | INVALID_INPUT`은 Candidate Search 축의 서로 다른 값에서만
추론할 수 있다. `NO_CANDIDATE → NOT_FOUND`인지 `NO_CANDIDATE → REVIEW_REQUIRED`인지는 승인된 문장이
없다.

따라서 이 slice는 그 사영을 구현하지 않는다. 약제별 `MedicationPreflightState`를 **입력**으로 받고
allowlist로 fail-closed 검증한다. DB 사영은 RAG-12-API(`#174`)가 Repository·Transaction과 함께
소유하며, 그때 별도 Decision으로 고정한다. Issue `#173`의 "검증: … 상태 allowlist"가 이 경계와 같다.

결론: **착수 가능**. 판정 함수는 순수하고, 소비할 enum 어휘는 확정 Target에 있으며, 미확정 사영은
입력 경계 밖으로 밀어냈다.

## 문제

Chat `ROUTINE`과 자동 Guide가 서로 다른 코드에서 "약품 식별이 끝났는가"를 판단하면, 한쪽이 미식별·
stale 처방으로 Rule·Retrieval·Provider를 실행할 수 있다. 현재 저장소에는 두 경로가 공통 소비할
결정적·부작용 없는 판정 경계가 없다. `MedicationIdentificationService.ensure_matched_for_preflight`는
DB 잠금과 `ApiError` 예외에 결속돼 있어 Worker Graph Node가 재사용할 수 없고, 예외 기반이라
`STALE_FALLBACK`과 `IDENTIFICATION_FALLBACK`을 서로 다른 code로 구분하지 못한다.

## 이번 변경의 목표

1. `PASS | IDENTIFICATION_FALLBACK | STALE_FALLBACK`을 내는 순수 결정 함수를 제공한다.
2. Identification Fallback과 Stale Fallback을 서로 다른 reason code로 분리한다.
3. 입력 순서와 무관한 canonical manifest hash를 고정한다.
4. 0개 약제·중복 ID·알 수 없는 상태·소유권 미검증을 예외가 아닌 fail-closed 판정으로 끝낸다.
5. DB·HTTP·lock·Retrieval·Provider 의존을 module import 수준에서 0건으로 유지한다.

## 비목표

- Repository·Migration·Snapshot persistence (`#174`)
- HTTP endpoint·Router·OpenAPI (`#174`)
- Candidate Search 상태 → Preflight 상태 사영 (`#174` + 별도 Decision)
- Runtime Bundle 내용 검증 (`#175` RAG-12A). 이 slice는 bundle **식별자 일치**만 본다
- fallback 문구·`execution_status`/`release_decision` 저장 조합 (`safety-result-v2.md`, `#180`)

## 모듈 경계

| 파일 | 역할 |
| --- | --- |
| `rag_runtime/identification_preflight.py` | 판정 kernel. stdlib만 import (`rag_runtime/__init__.py`에서 공개 심볼 export) |
| `ai_worker/tests/rag/test_identification_preflight.py` | 단위 테스트 |
| `tests/contract/rag/test_preflight_decision_contract.py` | 정본 문서 ↔ enum 어휘 drift, 순수성, 결정 matrix |
| `tests/fixtures/rag/preflight/decision_matrix.json` | 합성 결정 matrix fixture |

`ai_worker/tasks/rag/source_cleanup/preflight.py`는 Source Artifact 정리용이며 이 모듈과 무관하다.
이름 충돌을 피하기 위해 파일명을 `identification_preflight.py`로 둔다.

Guide 접수 Transaction은 Backend에, Chat `ROUTINE` preflight는 Worker Graph에 있다.
PR #382 리뷰(송은영)에서 backend 컨테이너가 `ai_worker/`를 COPY하지 않아 런타임에 깨지는 배포 결함이 지적되어,
`ocr_runtime` / `provider_runtime`과 동일하게 `rag_runtime/` 최상위 공용 패키지로 승격했다.
`backend/app/Dockerfile`과 `ai_worker/Dockerfile` 양쪽에서 COPY되어 Backend와 AI Worker 모두 동일한 커널을 소비할 수 있다.

## 입력 계약

```python
MedicationIdentificationPreflightRequest(
    currentness=PreflightCurrentnessToken(
        prescription_id,
        pinned_prescription_version_id,
        observed_active_prescription_version_id,
        pinned_runtime_release_bundle_id,
        observed_active_runtime_release_bundle_id,
        ownership_verified,
    ),
    medications=(MedicationSnapshotRef(prescription_version_medication_id, prescription_version_id, display_order), ...),
    identifications=(IdentificationSnapshotRef(
        prescription_version_medication_id,
        state,
        prescription_version_id,
        identification_id,
        code_system,
        canonical_code,
        runtime_release_bundle_id,
    ), ...),
)
```

- 모든 식별자는 소문자 하이픈 canonical UUID 문자열이다. 대문자·중괄호·공백 변형은 fail-closed다.
- `ownership_verified`는 호출자가 `prescription_version_medication → prescription_version → prescription
  → profile_id` 소유권 검증을 이미 끝냈다는 표시다. 이 kernel은 소유권을 판정하지 않는다.
- 약제 이름·함량·query 문자열은 입력이 아니다. 환자 식별 가능 값과 검색 원문을 manifest에 넣지 않는다.
- `state`는 `MATCHED` 또는 Graph의 6개 fallback 값만 허용한다.

## 판정 순서

```text
1. 구조 검증 실패 → execution_status=VALIDATION_ERROR, decision=IDENTIFICATION_FALLBACK,
                    reason=REVIEW_REQUIRED, manifest_hash=None
2. 현재성 불일치   → decision=STALE_FALLBACK, reason=EXECUTION_CONTEXT_STALE
3. 하나라도 non-MATCHED → decision=IDENTIFICATION_FALLBACK, reason=계약 표기 순서 우선값
4. 전체 MATCHED    → decision=PASS, reason=MATCHED
```

### 1단계 구조 검증 (fail-closed)

`ownership_verified`가 `True`가 아님 / 약제 0개 / 약제 ID 중복 / `display_order` 비양수 또는 중복 /
약제 집합과 Identification 집합 불일치 / Identification ID 중복 / canonical UUID 위반 /
약제 `prescription_version_id`가 pinned version과 불일치 / `MATCHED`인데
`identification_id`·`code_system`·`canonical_code` 중 누락 / 비-`MATCHED`인데 그 값이 존재 /
`state`가 allowlist 밖.

`state`는 `StrEnum`이므로 알 수 없는 문자열은 `MedicationPreflightState(...)` 생성 시점에 걸린다.
kernel은 그와 별개로 런타임 타입도 확인해 `str` raw 값이 흘러들어와도 판정으로 끝낸다.

VALIDATION_ERROR가 `IDENTIFICATION_FALLBACK/REVIEW_REQUIRED`로 끝나는 이유: Graph에는 3개 분기만
있고, 구조 오류는 "전체 약제 `MATCHED`를 확인할 수 없음"이므로 계약이 이미 가진 가장 일반적인
검토 요구 code로 사영하는 것이 새 상태를 만들지 않는 유일한 선택이다. 원인은 공개 DTO가 아닌
내부 `validation_codes`로만 남긴다.

### 2단계 현재성 (Stale)

- `observed_active_prescription_version_id != pinned_prescription_version_id`
- `observed_active_runtime_release_bundle_id != pinned_runtime_release_bundle_id`
- Identification snapshot의 `prescription_version_id != pinned_prescription_version_id`
- `MATCHED` Identification의 `runtime_release_bundle_id != pinned_runtime_release_bundle_id`

`prescription-version-v1.md`의 "활성 여부는 `prescription.active_version_id`와의 일치로 판정한다"를
그대로 쓴다. 별도 `is_current` 컬럼을 가정하지 않는다.

**Stale이 Identification Fallback보다 앞선다.** Graph는 두 분기의 우선순위를 고정하지 않았으나,
pinned context가 이미 현재가 아니면 그 위에서 계산한 약제별 상태 자체를 신뢰할 수 없다. 되돌릴 수
있는 판단이고 두 분기 모두 일반 RAG 실행을 차단하므로 안전 측면의 차이는 없다. `#174` 리뷰에서
반대 결론이 나오면 이 함수의 2·3단계 순서만 바꾸면 된다.

### 3단계 Identification Fallback 대표 reason

여러 약제가 서로 다른 이유로 막히면 대표 reason이 필요하다. 저장소에 승인된 severity 순서가 없으므로
**계약 문장의 표기 순서**를 그대로 우선순위로 쓴다.

```text
REVIEW_REQUIRED > AMBIGUOUS > UNAVAILABLE > NOT_FOUND > INVALID_INPUT > UNRESOLVED
```

정보 손실을 막기 위해 outcome은 대표 reason과 함께 등장한 전체 집합(`identification_reasons`)과
막힌 약제 ID(`blocking_medication_ids`)를 계약 순서·사전순으로 함께 반환한다. `#174`가 어떤 값을
저장·노출할지는 이 kernel이 고정하지 않는다.

**미확정 항목**: 대표 reason 규칙은 승인된 Decision이 아니다. `#174` 병합 전 송은영·권가빈 검토가
필요하다. 그때까지 이 값을 공개 DTO나 환자 문구에 직접 매핑하지 않는다.

## Canonical manifest hash

```text
sha256(
  json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
)
```

payload는 다음만 포함한다.

- `projection_version`: `medication-identification-preflight-manifest-v1`
- `prescription_id`, `prescription_version_id`, `runtime_release_bundle_id` (pinned 값)
- `medications`: `(display_order, prescription_version_medication_id)` 오름차순 정렬 배열. 각 원소는
  `prescription_version_medication_id`, `display_order`, `state`, `prescription_version_id`, `identification_id`, `code_system`,
  `canonical_code`, `runtime_release_bundle_id`

관측된 active 포인터와 `ownership_verified`는 payload에 넣지 않는다. manifest는 "이 pinned 입력 집합"의
정체성이고 외부 현재성 관측은 결과 플래그에 반영되므로, 같은 입력 집합이 stale인지 여부에 따라 hash가 달라지면
안 된다. 반면 판정 decision input으로 소비되는 식별 스냅샷의 `prescription_version_id`와 `runtime_release_bundle_id`는
provenance로서 medication 항목에 포함되어, 서로 다른 식별 스냅샷에 대한 판정(PASS ↔ STALE)을 hash가 고유하게
재현할 수 있도록 보장한다 (Review [P2]). 문자열은 Unicode NFC를 요구하고 조용히 변환하지 않는다(`medication-identification-v1.md`의
NFC 경계와 동일).

구조 검증 실패 시 `manifest_hash=None`이다. 신뢰할 수 없는 입력으로 안정적 정체성을 만들지 않는다.

## 출력 계약

```python
MedicationIdentificationPreflightOutcome(
    execution_status,            # EVALUATED | VALIDATION_ERROR
    decision,                    # PASS | IDENTIFICATION_FALLBACK | STALE_FALLBACK
    reason,                      # MATCHED | 6개 fallback | EXECUTION_CONTEXT_STALE
    manifest_projection_version,
    manifest_hash,               # str | None
    medication_count,
    matched_count,
    identification_reasons,      # tuple, 계약 순서
    blocking_medication_ids,     # tuple, 사전순
    stale_signals,               # tuple, 선언 순서 (PRESCRIPTION_STALE, IDENTIFICATION_STALE, RUNTIME_RELEASE_STALE)
    primary_stale_projection,    # PreflightStaleProjection | None, 단일 집계 대표 사영
    validation_codes,            # tuple, 선언 순서. 내부 진단
)
```

### Downstream STALE 사영 계약

`safety-result-v2.md` "STALE과 공개 오류"에 따라 판정 신호를 downstream 공개 fallback_code 및 내부 stale_reason으로 사영하는 순수 함수를 함께 제공한다.

```python
PreflightStaleProjection(fallback_code: str, stale_reason: str | None)
project_preflight_stale_signal(signal: PreflightStaleSignal) -> PreflightStaleProjection
project_preflight_stale_signals(signals: tuple[PreflightStaleSignal, ...]) -> PreflightStaleProjection | None
```

- `PRESCRIPTION_STALE` → `fallback_code="PRESCRIPTION_STALE"`, `stale_reason=None`
- `IDENTIFICATION_STALE` → `fallback_code="EXECUTION_CONTEXT_STALE"`, `stale_reason="IDENTIFICATION_STALE"`
- `RUNTIME_RELEASE_STALE` → `fallback_code="EXECUTION_CONTEXT_STALE"`, `stale_reason="RUNTIME_RELEASE_STALE"`

### 복합 STALE 신호 집계 및 우선순위 규칙

처방 Version과 런타임 Bundle/식별 불일치가 동시에 발생하는 경우, `docs/contracts/targets/post-mvp-1/safety-result-v2.md`의 "STALE과 공개 오류 - 복합 STALE 우선순위와 단일 오류 사영" 정본 계약에 따라 다음의 결정적 우선순위로 집계하여 `outcome.primary_stale_projection`에 단일 사영을 고정한다 (Review [P1]).

1. **`PRESCRIPTION_STALE` 최우선**: 사용자의 활성 처방전 버전 자체가 변경된 임상 사건은 환자에게 직접 안내되어야 하는 근본 원인이므로, 시스템 내부적 컨텍스트 불일치보다 항상 우선한다 (`fallback_code="PRESCRIPTION_STALE"`, `stale_reason=None`).
2. **`IDENTIFICATION_STALE` 우선**: 처방 버전 변경이 없을 때, 약제 단위의 공식 의약품 식별 불일치가 런타임 번들 불일치보다 상위 도메인 사유로 취급된다 (`fallback_code="EXECUTION_CONTEXT_STALE"`, `stale_reason="IDENTIFICATION_STALE"`).
3. **`RUNTIME_RELEASE_STALE`**: 활성 런타임 번들만 변경된 경우 (`fallback_code="EXECUTION_CONTEXT_STALE"`, `stale_reason="RUNTIME_RELEASE_STALE"`).

후속 소비자(#174 RAG-12-API)는 복수 신호를 임의로 사영하거나 선언 순서에 의존하지 않고 정본 계약에 고정된 `outcome.primary_stale_projection`을 직접 소비한다.

`execution_status`를 별도 축으로 둔 이유는 `ai_worker/tasks/rag/evidence_gate.py`의
`EvidenceGateExecutionStatus` 선례와 같다. 공유 결정축에 새 값을 만들지 않고 구조 오류를 구분한다.

모든 필드는 frozen dataclass·tuple·enum이다. 호출자가 outcome을 변형할 수 없다.

## 부작용 없음의 증명 방식

- 함수 signature에 port·session·client 매개변수가 없다. 주입할 지점 자체가 없다.
- module의 import 집합이 stdlib뿐임을 계약 테스트가 고정한다. `sqlalchemy`, `httpx`, `app.*`,
  `ai_worker.tasks.rag.evidence_retrieval`, provider·retrieval module이 하나라도 들어오면 실패한다.
- 시계·난수를 쓰지 않는다. 만료 판정은 이 slice의 범위가 아니다(`expires_at`은 Candidate Search 축).
- 같은 입력 재호출이 동일 outcome을 만드는지 테스트가 확인한다.

## 필수 테스트 대응

| Issue 요구 | 테스트 |
| --- | --- |
| 전체 MATCHED → PASS | `test_all_matched_returns_pass` |
| 하나라도 non-MATCHED → Identification Fallback, Retrieval/Provider 0건 | `test_single_non_matched_blocks_execution`, `test_kernel_has_no_execution_ports` |
| 현재 Version 변경 → Stale Fallback | `test_active_version_change_returns_stale_fallback` |
| Medication 0개·중복 ID·알 수 없는 enum → fail-closed | `test_empty_medications_fails_closed` 외 |
| 순서가 달라도 동일 hash/decision | `test_input_order_does_not_change_manifest_or_decision` |
| 동일 입력 재시도 → 동일 결과, side effect 0건 | `test_repeated_evaluation_is_idempotent` |

## 위험

| 위험 | 완화 |
| --- | --- |
| 대표 reason 규칙이 리뷰에서 뒤집힘 | 전체 집합을 함께 반환. 대표값만 바꾸면 됨 |
| Stale 우선순위가 리뷰에서 뒤집힘 | 판정 단계가 분리돼 있어 2·3단계 교체로 끝남 |
| Backend가 `ai_worker`를 import해야 함 | 이 slice는 배선 없음. `#174`에서 배치 결정 |
| pure 테스트가 승인·공개 가능성으로 오해됨 | 설계·증빙 문서와 module docstring에 `PUBLIC_TRACK_F_ENABLED=false`와 미구현 선행을 명시 |
