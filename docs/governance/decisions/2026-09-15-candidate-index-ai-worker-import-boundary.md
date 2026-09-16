# Product Decision: RAG-07B Candidate Index build의 `backend` → `ai_worker` import 허용 확장

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-168-20260915` |
| 상태 | Approved |
| 제안일 | 2026-09-15 |
| 승인일 | 2026-09-15 |
| 제안·구현 | 송은영 (`@phina-io`) |
| 책임 리뷰 | 정현우 (`@ceohwj`) — RAG-07A Manifest·search contract, PD-175 원 제안자 |
| 추적 Issue·PR | [#168](https://github.com/AI-HealthCare-05/AH_05_04/issues/168) · [PR #576](https://github.com/AI-HealthCare-05/AH_05_04/pull/576) |
| 승인 근거 | PR #576 reviewer approval and active tests/contract/test_backend_ai_worker_import_boundary.py allowlist contract test |
| 선행 Decision | [`PD-175-20260910`](2026-09-10-runtime-bundle-canonical-configuration-persistence.md) — `backend`가 `ai_worker`의 순수 kernel 모듈 하나만 production import한다는 (A)안을 확정하고, 그 경계를 지키는 계약 테스트(`tests/contract/test_backend_ai_worker_import_boundary.py`) 도입을 후속 조건으로 남겼다. |

## 목적

PD-175가 고정한 `ALLOWED_AI_WORKER_MODULES` 허용 목록(현재 `ai_worker.tasks.rag.runtime_bundle_builder` 1개)을, RAG-07B(#168) Candidate Index build 진입점이 필요로 하는 2개 모듈까지 확장한다.

## 배경 — 계약 테스트가 막은 지점

PR #576의 `test-contract` CI가 다음으로 실패했다.

```
tests/contract/test_backend_ai_worker_import_boundary.py::test_backend_production_code_imports_only_allowed_ai_worker_modules
허용 목록 밖의 ai_worker 모듈을 backend production 코드가 import합니다:
{'backend/app/services/rag_candidate_index_build.py': ['ai_worker.tasks.rag.candidate_index', 'ai_worker.tasks.rag.catalog.export']}
```

`backend/app/services/rag_candidate_index_build.py`는 RAG-07A(#167)가 공개한 유일한 build 진입점 `build_candidate_index()`(`ai_worker/tasks/rag/candidate_index.py`)를 호출한다. 이 함수의 첫 번째 파라미터 타입 `CatalogExportArtifacts`는 `ai_worker/tasks/rag/catalog/export.py`에 있다. 이 서비스가 `backend`에서 `ai_worker`를 production import하는 **두 번째 사례**이며, PD-175의 "하나만" 원칙을 그대로는 만족하지 못한다.

## 결정

**허용 목록을 다음 2개 모듈로 확장한다.**

| 모듈 | 근거 |
| --- | --- |
| `ai_worker.tasks.rag.candidate_index` | RAG-07A의 유일한 public build 진입점. Repository가 아니라 이 진입점을 호출해야 RAG-07A의 판정(catalog envelope·config validity·member 무결성)을 우회하지 못한다. |
| `ai_worker.tasks.rag.catalog.export` | 위 진입점의 필수 파라미터 타입(`CatalogExportArtifacts`) 하나만 쓴다. |

두 모듈 모두 PD-175가 `runtime_bundle_builder`에 적용한 것과 같은 기준을 충족하는지 이번에 직접 확인했다.

- **I/O·시계·session 없음.** 두 모듈과 그 전체 import 체인(`catalog.approval`·`catalog.build`·`catalog.component_observation`·`catalog.normalize`·`catalog.types`·`catalog.validate`)을 AST로 전수 추적한 결과, `ai_worker` 바깥 의존은 0건이다(표준 라이브러리만 사용). PD-175가 검증한 `runtime_bundle_builder` 체인과 동일한 성질이다.
- **배포 이미지.** `backend/app/Dockerfile`은 PD-175 때 이미 `COPY ./ai_worker ./ai_worker`를 추가했고, `tests/contract/test_backend_image_rag_runtime.py`가 이를 이미지 기준으로 검증한다. 두 모듈 모두 이미 COPY되는 `ai_worker/tasks/rag/` 하위 경로에 있어 별도 Dockerfile 변경이 필요 없다.
- **의존성 그룹.** 외부 의존이 없으므로 `app` 그룹으로 충분하고 `worker` 그룹(asyncpg·boto3·httpx)은 필요 없다. `pyproject.toml` 변경 없음.
- **역방향 금지 유지.** `ai_worker → backend` 금지는 Worker 테스트 lane이 `backend`를 PYTHONPATH에서 제외한 별도 프로세스로 강제하는 기존 장치 그대로다. 이번 변경은 이 방향에 영향을 주지 않는다.

## 검토한 대안

| 대안 | 판단 |
| --- | --- |
| `CatalogExportArtifacts`를 backend에 로컬 재정의(구조적으로 동일한 Protocol/dataclass)해서 두 번째 import를 피한다 | 기각. RAG-07A의 계약 타입을 그대로 쓰지 않고 backend에 재선언하면, RAG-07A가 그 타입을 바꿀 때 backend가 조용히 stale해질 수 있다. `catalog.export`는 그 자체로 순수·stdlib-only이므로 재정의로 얻는 격리 이득보다 정본 이중화 비용이 크다. |
| `TYPE_CHECKING` 블록으로 두 번째 import를 감싸 계약 테스트를 피한다 | 기각. 계약 테스트는 AST 전체를 훑어 `TYPE_CHECKING` 안의 import도 그대로 잡아낸다(우회 불가하도록 의도된 설계). 우회 가능하더라도 실제 런타임 의존은 그대로 존재하므로 검사를 무력화할 뿐 실질적 결합은 줄어들지 않는다. |
| PD-175처럼 kernel을 `rag_runtime/`(backend·ai_worker 공용 최상위 패키지)로 옮긴다 | 보류. PD-175도 같은 이유로 이 대안을 기각했다(`evaluate_snapshot_use_eligibility`·Catalog 상태 enum까지 옮겨야 해 범위 초과). RAG-07A 쪽은 이전 대상이 더 넓어(`candidate_index.py` 전체와 `catalog/` 패키지) 기각 근거가 더 강하다. |
| Repository가 `build_candidate_index()`를 호출하지 않고 Service가 이미 판정된 결과만 받는다 | 기각. PD-175 §6이 정확히 이 실패 모드("판정이 권고에 머문다")를 이미 겪었다. Service가 진입점을 직접 호출해야 RAG-07A의 fail-closed 판정을 우회할 수 없다. |

## 이 승인이 부여하지 않는 것

- **일반적인 `ai_worker` import 허용 아님.** 위 2개 모듈 외의 `ai_worker` 모듈을 `backend` production 코드가 import하려면 별도 Decision이 필요하다.
- **RAG-08/RAG-09의 import 권한 아님.** `backend/app/repositories/rag_candidate_index_repository.py`(read port)는 이 Decision과 무관하게 `ai_worker` import 0건을 유지하며, RAG-08/RAG-09는 이 read port로만 조회한다.
