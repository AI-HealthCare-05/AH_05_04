# RAG-12A Runtime Bundle Build 설계 (Issue #175)

| 항목 | 값 |
| --- | --- |
| 대상 Issue | [#175](https://github.com/AI-HealthCare-05/AH_05_04/issues/175) |
| 구현 담당자 | 정현우 (`@ceohwj`) |
| 담당 리뷰어 | 송은영 (`@phina-io`) — persistence·FK·transaction |
| Product/Safety 리뷰 | 권가빈 (`@hazelnutflavoured`) |
| 상위 계약 | [`targets/post-mvp-1/rag-runtime-v1.md`](../../contracts/targets/post-mvp-1/rag-runtime-v1.md) (Approved Target · Not implemented) |
| 문서 상태 | 구현 설계 · 계약 변경 없음 |

## 1. 착수 판단 근거

`#175`의 「진행 기준」은 선행 Issue의 Close 여부가 아니라 **확정 Contract·Receipt·Interface 확보 범위**로 착수를 판단한다. develop 기준 실측 결과는 다음과 같다.

| 선행 | 확정 여부 | 근거 (develop 머지 코드) |
| --- | --- | --- |
| RAG-03~06 Source | 확정 | `rag_source`, `rag_source_endpoint`, `rag_source_operation`, `rag_source_snapshot`, `rag_source_snapshot_verification` (`backend/app/models/rag_source.py`) |
| RAG-06 Catalog | 확정 | `catalog_version` · `catalog_manifest_hash` (`ai_worker/tasks/rag/catalog/`, `CandidateIndexMember`) |
| RAG-07A Candidate Index | 확정 | `CandidateIndexManifest.index_version` · `content_hash` · `member_set_hash` · `configuration_hash` |
| RAG-07B Evidence | 확정 | `rag_evidence*` (`backend/app/models/rag_evidence.py`) |
| RAG-12 Preflight | 확정 | `rag_runtime/identification_preflight.py` (#382) + `PD-173-20260910` Approved (2026-09-10) |
| RAG-12A 저장 구조 | 확정 | `rag_runtime_execution_manifest`, `rag_runtime_release_bundle`, `rag_runtime_bundle_source`, `rag_runtime_environment`, `rag_runtime_environment_transition`, `rag_release_evaluation_approval` (#164 / migration `164b6c7d8e9f`) |
| **Bundle build kernel** | **0건** | develop 전체에 Runtime Execution Manifest 구성 로직과 `BUILDING` 전이가 없다. `RagRuntimeRepository`는 단건 CRUD만 제공한다. |

즉 이 Issue는 **확정된 테이블 인터페이스 위에서 비어 있는 build kernel을 채우는 작업**이며, 팀 컨벤션의 「확정 Interface 기반 구현은 병행 가능」에 해당한다. 추정 계약 위에서 구현하는 범위는 없다.

## 2. 차단 항목 — Worker 호환성 검사는 이 PR 범위에서 제외한다

`#175`의 「API 구현 점검 · 검증」 항목 중 **`Worker compatibility contract`** 는 착수할 수 없다.

- [`docs/governance/post-mvp-1-document-authority.md`](../../governance/post-mvp-1-document-authority.md) 「구현 전 재결정이 필요한 충돌」의 **Runtime Bundle과 Worker 배포** 항목이 아직 해소되지 않았다. 같은 절의 `PD-91-20260831`·`PD-141-20260902` 항목에는 「더 이상 미정 충돌로 취급하지 않는다」는 해소 문구가 있으나, Runtime Bundle 항목에는 없다.
- 해당 항목은 `RETRY_WAIT` 처리, **Worker–Bundle 호환성 검사**, drain/rolling deployment 방식을 **함께** 확정하도록 요구하며, 확정 전까지 「관련 구현과 `current/` 승격을 차단한다」고 명시한다.
- `rag-runtime-v1.md`도 같은 내용을 반복한다: 「… 승인되기 전에는 Runtime Bundle을 Current로 승격하지 않는다.」

`AGENTS.md`는 경계가 정의되지 않은 경우 규칙을 발명하지 말고 소유자에게 확인하도록 요구한다. 따라서 이 PR은 Worker 호환성 판정 규칙을 **만들지 않는다**. 대신:

- `worker_artifact_ref`는 Manifest에 **불변 값으로 고정(pin)만** 한다. 컬럼은 #164에서 이미 머지된 구조이므로 고정 자체는 새 계약이 아니다.
- 호환성 **판정**은 kernel 결과의 `deferred_checks`에 `WORKER_BUNDLE_COMPATIBILITY`로 명시 기록하고, 차단 코드 `BLOCKED_BY_RUNTIME_BUNDLE_WORKER_DEPLOYMENT_DECISION`을 남긴다.
- 이 항목은 `#175`의 「완료 기준」 6개 어디에도 필요하지 않다. 6개 기준은 모두 `BUILDING` 범위에서 충족 가능하다.
- 이 PR은 `rag-runtime-v1.md`를 `current/`로 승격하지 않는다. 계약 문서 상태 변경은 0건이다.

Worker 호환성 판정과 `current/` 승격은 `#91` 후속 Product Decision 승인 후 별도 작업으로 남는다. 따라서 이 PR 병합만으로 `#175`를 Close하지 않고, 위 차단 코드를 기록한 Open 상태를 유지한다.

## 3. 해결하려는 문제

Source·Catalog·Index·Rule·Guideline·Safety의 버전을 각각 "최신값"으로 읽으면 평가 대상과 실제 실행 대상이 달라진다. 평가와 실행이 같은 대상을 가리키도록, member set을 한 번 고정한 **불변 Runtime Execution Manifest**와 그 Manifest를 참조하는 **`BUILDING` Bundle**을 만든다.

## 4. 책임 경계

```
[호출자: 평가 Runner / RAG-17 활성화 흐름]
        │  승인된 component ID/version/hash 입력
        ▼
ai_worker/tasks/rag/runtime_bundle_builder.py   ← 순수 kernel (I/O·시계·락 없음)
        │  RuntimeBundleBuildOutcome (BUILDABLE | REJECTED)
        ▼
backend/app/repositories/rag_runtime_repository.py::build_runtime_bundle()
        │  단일 transaction: manifest + bundle(BUILDING) + bundle_source 전량
        ▼
rag_runtime_execution_manifest / rag_runtime_release_bundle / rag_runtime_bundle_source
```

- kernel은 판정과 hash만 담당한다. `identification_preflight.py`(#173)와 같은 규약을 따른다: I/O·락·시계·port 없음, 거부 입력에서 예외를 던지지 않고 **typed fail-closed 결과**로 끝낸다. 예외 경로를 실행 허가로 오인할 수 없게 한다.
- repository는 저장만 담당한다. 판정을 다시 하지 않고, `BUILDABLE`이 아닌 결과는 저장하지 않는다.
- 새 Service 계층·Protocol·Factory는 만들지 않는다. 실제 구현체와 소비자가 각각 하나뿐이므로 `CONTRIBUTING.md`의 최소 복잡성 기준을 따른다. build port는 「kernel 판정 → repository transaction」 조합 그 자체이며, 통합 테스트가 이 조합을 고정한다.

## 5. Member 모델 — 머지된 스키마를 그대로 사용한다

`#175`가 열거한 member(Source·Corpus·Medication Catalog·Evidence Index·Candidate Index·Rule·Guideline·Safety)를 새 컬럼·새 테이블 없이 #164 구조에 사상한다.

### 5.1 Source-backed member → `rag_runtime_bundle_source`

`RagRuntimeSourcePurpose`(머지된 enum)를 member 어휘의 정본으로 사용한다.

| #175 member | `source_purpose` | 필수 |
| --- | --- | --- |
| Medication Catalog | `CATALOG` | 필수 |
| Corpus / Evidence Index 근거 | `KNOWLEDGE` | 선택 |
| Candidate Index 입력 | `CANDIDATE_INDEX_INPUT` | 선택 |
| Rule 근거 | `RULE` | 선택 |
| Guideline 근거 | `GUIDELINE` | 선택 |
| Safety policy 근거 | `SAFETY_POLICY` | 선택 |

각 member는 `rag_source_snapshot`을 가리키고, kernel 입력으로 그 snapshot의 `source_version`·`canonical_checksum`·승인·freshness·revocation·scope·environment 관측값을 함께 받는다.

#### Snapshot 승인·Freshness 판정은 #362의 공용 함수를 재사용한다

`ai_worker/tasks/rag/source_ingestion/snapshot_lifecycle.py`의 `evaluate_snapshot_use_eligibility`는 docstring 그대로 **「Catalog·Runtime 공통 Snapshot 사용 가능 판정」**이며, #362에서 머지된 뒤 **production 호출자가 0건**이었다. 이 Issue의 Bundle build가 그 함수의 첫 Runtime 소비자다.

따라서 kernel은 승인·freshness를 자체 판정하지 않고 그 함수에 위임한다.

- `RuntimeBundleSourceMemberInput`의 관측 필드는 그 함수의 인자와 정확히 일치한다: `verification_status`, `rejected_record_count`, `publication_approval_passed`, `freshness_eligible`, `provenance_valid`.
- 실패 사유는 `SnapshotUseFailureCode`를 그대로 노출한다(`snapshot_use_failure_codes`). `RuntimeBundleRejectionReason`에는 우산 사유 `MEMBER_SNAPSHOT_NOT_USABLE` 하나만 둔다.
- 그 함수가 모델링하지 않는 축(`approval_expired`, `revocation_unresolved`, `scope_allowed`, environment)만 kernel이 판정한다.
- 그 함수는 음수 `rejected_record_count`에 `ValueError`를 던지므로, kernel은 호출 전에 shape 검증으로 걸러 **어떤 입력에서도 예외를 던지지 않는다**는 규약을 유지한다.
- Artifact member는 source snapshot이 아니므로 이 정책의 적용 대상이 아니다. 자체 승인 축을 별도로 검사한다.

이렇게 하면 Catalog build와 Bundle build의 snapshot 정책이 갈라질 수 없다. 초기 구현은 자체 boolean(`approval_effective`/`snapshot_freshness_current`/`snapshot_complete`)으로 병행 정책을 만들었는데, `SNAPSHOT_SUPERSEDED`(STALE)와 `rejected_record_count > 0 && !publication_approval_passed` 규칙을 누락하고 있었다. `CONTRIBUTING.md`의 「기존 구조와 유틸리티로 해결할 수 있다면 이를 재사용한다」 위반이자 실제 판정 누락이었으므로 교체했다.

### 5.2 Artifact member → Bundle 행 컬럼

| #175 member | 컬럼 | 필수 |
| --- | --- | --- |
| Candidate Index | `candidate_index_ref` + `candidate_index_manifest_hash` | 필수 |
| Evidence(Knowledge) Index | `knowledge_index_ref` + `knowledge_index_manifest_hash` | 선택 |
| Rule set | `rule_set_ref` | 선택 |
| Guideline set | `guideline_set_ref` | 선택 |
| Safety policy | `safety_policy_ref` | 선택 |

Candidate Index의 `manifest_hash`는 `CandidateIndexManifest.content_hash`(#167)를 그대로 고정한다. 새 hash 체계를 만들지 않는다.

### 5.3 필수 member 판정

완료 기준이 명시한 필수 member는 **Medication Catalog**와 **Candidate Index** 둘이다. Rule·Guideline·Safety는 `BUILDING` 생성에는 필수가 아니고, 없으면 `readiness_blockers`에 기록되어 `READY` 승격을 차단한다(§7).

### 5.4 머지된 스키마가 덮지 못하는 축 — Graph·Validator (리뷰 필요)

`rag-runtime-v1.md` 「Runtime Release Bundle」은 Bundle이 **Resolver·Graph·Prompt·Validator·Model**을 함께 고정한다고 적었다. 그런데 #164에서 머지된 `rag_runtime_execution_manifest`의 컬럼은 `worker_artifact_ref`, `model_ref`, `prompt_ref`, `parser_ref`, `resolver_ref`, `guard_policy_ref`뿐이다.

- **Graph**: 대응 컬럼이 없다. LangGraph Node/Edge 정본 version을 고정할 자리가 Manifest에 없다.
- **Validator**: `parser_ref`·`guard_policy_ref`가 근사값일 수 있으나 `validator_ref`는 없다. 어느 컬럼이 정본인지 문서가 정하지 않았다.

이 PR은 **컬럼을 추가하지 않는다.** 새 컬럼은 마이그레이션과 Manifest hash 정의 변경을 동반하고, 그것은 `AGENTS.md`가 요구하는 새 Decision 또는 Contract Freeze version 사안이다. 따라서 kernel은 머지된 컬럼만 고정하고, 이 두 축은 **미고정 상태로 남는다**.

`#175` 완료 기준 6개는 이 두 축 없이 충족된다. 다만 「Graph version을 Bundle에 고정한다」는 계약 문장은 현재 스키마로 충족 불가하므로, RAG-17 승격 전에 `송은영`·`권가빈` 확인이 필요하다. Manifest에 `graph_ref`/`validator_ref`를 추가할지, 아니면 계약 문장을 개정할지는 이 Issue가 단독으로 정할 사안이 아니다.

### 5.5 환경 일치

`rag_source_snapshot`에는 environment 컬럼이 없다. 따라서 환경은 **DB에서 추론하지 않고** build 입력의 `target_environment`와 member별 `observed_environment`를 비교한다. 이는 `source_governance.py`의 `expected_environment`/`observed_environment` 쌍과 같은 방식이며, 새 컬럼·새 마이그레이션을 추가하지 않는다. Bundle↔환경의 물리적 일치는 #164에서 이미 머지된 composite FK(`(active_bundle_id, active_bundle_manifest_hash)`)가 보장한다.

## 6. Canonical serialization과 결정적 hash

두 개의 hash를 만든다. 둘 다 SHA-256 hex 64자이며, 머지된 CHECK 제약(`length(...) = 64`)을 만족한다.

1. **`manifest_hash`** — `rag_runtime_execution_manifest.manifest_hash`. 실행 환경 축(schema/git/worker artifact/model/prompt/parser/resolver/guard policy)의 동일성.
2. **`bundle_manifest_hash`** — `rag_runtime_release_bundle.bundle_manifest_hash`. Manifest + 전체 member set의 동일성.

### Canonical 규칙

`identification_preflight.py`와 `source_governance.py`가 이미 쓰는 규칙을 그대로 재사용한다. 새 규칙을 만들지 않는다.

- `json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`
- 문자열은 NFC 정규화
- member 목록은 **canonical JSON 바이트 기준 정렬** → 입력 순서와 무관한 동일 hash
- 각 payload에 projection version 상수를 포함해 향후 정의 변경을 hash로 구분

```
RUNTIME_BUNDLE_MANIFEST_PROJECTION_VERSION = "rag-runtime-bundle-manifest-v1"
```

관측 시점 값(`observed_environment`, freshness 관측치, 승인 관측치)은 **hash 입력에서 제외**한다. Manifest는 *고정된 member set*의 동일성을 식별해야 하며, 같은 member set이 관측 상태 때문에 다른 hash를 갖게 되면 평가·실행 대상 동일성 판정이 깨진다. #173의 `canonical_preflight_manifest_hash`가 관측 pointer를 제외한 것과 같은 이유다.

## 7. `READY` 차단과 소유 경계

| 전이 | 소유 |
| --- | --- |
| `BUILDING` 생성 | **이 Issue** |
| build 실패 → `FAILED` | **이 Issue** |
| `BUILDING → READY`, `RETIRED` | RAG-17 |
| 환경 `ACTIVE`/`SUSPENDED`, active pointer 전환, Rollback 실행 | RAG-17 |

- kernel은 `READY`를 **반환할 수 없다**. `RuntimeBundleBuildDecision`에 `READY`에 대응하는 값이 없다.
- repository의 build transaction은 `bundle_status=BUILDING`만 기록하고, `rag_runtime_environment`·`rag_runtime_environment_transition`을 **읽거나 쓰지 않는다**. 따라서 build 실패가 기존 active pointer를 바꿀 경로가 구조적으로 없다.
- Rule·Guideline·Safety·Knowledge Index가 없으면 `readiness_blockers`에 기록한다. 이 값은 RAG-17이 `READY` 판정에서 읽을 입력이며, 이 PR에서는 판정하지 않는다.
- 「`BUILDING` member 입력을 임의로 update하지 못한다」는 repository에 member update/delete 메서드를 두지 않고, build transaction이 member 전량을 한 번만 생성하는 것으로 만족한다. 기존 `bundle_manifest_hash` unique 제약이 동일 member set 재삽입을 막는다.

## 8. 실패 시 동작

- kernel이 `REJECTED`면 repository를 호출하지 않는다. 저장 0건.
- repository transaction 중 예외가 발생하면 manifest·bundle·member 전량이 rollback된다. 부분 Bundle·부분 member 0건.
- Manifest는 `manifest_hash` unique로 재사용한다. 동일 실행 환경 축으로 두 Bundle을 만들 때 Manifest 행이 중복 생성되지 않는다. 단 `manifest_hash`는 호출자가 넘기는 값이므로, 같은 hash로 **다른 내용**의 Manifest가 이미 있으면 조용히 재사용하지 않고 `RagRuntimeExecutionManifestConflictError`로 fail-closed한다. 그렇지 않으면 호출자가 고정하지 않은 실행 축에 Bundle이 묶여, 이 Issue가 막으려는 평가–실행 대상 drift가 그대로 발생한다.
- active pointer는 어느 경로에서도 접근하지 않는다(§7).

## 9. 검증

| 완료 기준 | 검증 위치 |
| --- | --- |
| 동일 구성요소 → 동일 Bundle/Manifest Hash | `ai_worker/tests/rag/test_runtime_bundle_builder.py` (순서 무관 동일성, 값 변경 시 상이성) |
| Medication Catalog·Candidate Index 필수 member | 동일 파일 (누락 시 `REJECTED`) |
| 미승인·만료·누락·revoked·환경 불일치 차단 | 동일 파일 (사유별 케이스) |
| `BUILDING` member 임의 update 불가 | `backend/app/tests/rag/test_runtime_bundle_repository.py` |
| 하위 component 미완료 시 `READY`·active pointer 0건 | 동일 파일 + `tests/integration/rag/test_runtime_bundle_build.py` |
| build failure이 active pointer 미변경 | `tests/integration/rag/test_runtime_bundle_build.py` |

테스트 명령은 `#175`가 지정한 3개를 그대로 사용한다.

## 10. 공유 계약 영향

- 환자용 API·공개 DTO·OpenAPI 변경 **0건**.
- 마이그레이션·DB 스키마 변경 **0건**. #164에서 머지된 구조만 사용한다.
- `docs/contracts/` 상태 변경 **0건**. `rag-runtime-v1.md`는 `targets/`에 그대로 둔다(§2).
- 새 enum·status·column·queue **0건**. member 어휘는 머지된 `RagRuntimeSourcePurpose`를, snapshot 사용 가능 판정은 머지된 `evaluate_snapshot_use_eligibility`와 `SnapshotUseFailureCode`를 사용한다.
- 미충족으로 남는 계약 문장 2건: Worker 호환성 검사(§2), Graph·Validator version 고정(§5.4).
- kernel 내부 진단 코드(`RuntimeBundleRejectionReason` 등)는 공개 DTO에 사상하지 않는다. `identification_preflight.py`의 `PreflightValidationCode`와 같은 취급이다.
