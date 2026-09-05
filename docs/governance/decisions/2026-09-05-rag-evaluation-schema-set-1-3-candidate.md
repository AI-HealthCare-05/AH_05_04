# Product Decision Candidate: RAG Evaluation Schema Set 1.3

| 항목 | 값 |
| --- | --- |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-05 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation 책임 리뷰어 |
| 추적 Issue | [#273](https://github.com/AI-HealthCare-05/AH_05_04/issues/273) |
| 적용 범위 | Post-MVP-1 Track F Evaluation provenance 계약 확장 후보 |

## 후보 결정

`rag-eval.schema-set@1.3.0`을 자연어 Retrieval 평가의 provenance graph에 필요한 세 계약을 포함하는
후보 Schema Set으로 제안한다. 이 후보의 승인 전환에는 권가빈 (`@hazelnutflavoured`)의 실제 Pull Request
review event가 필요하며, 문서와 schema export의 존재만으로 그 event를 대신할 수 없다.

| Immutable field | 값 |
| --- | --- |
| Schema Set ID | `rag-eval.schema-set` |
| Schema Set version | `1.3.0` |
| Schema Set SHA-256 | `654416197159bf46620b7b875d05a07dbf51d3693d770e8a1b027c7a0b3deb77` |
| Canonical member root | `evals/schemas/1.3.0/` |
| Member count | `21` |

Schema Set hash는 member별 `{schema_id, schema_version, schema_sha256}`를 정렬한 canonical JSON의
SHA-256이다. Set version과 member version은 독립적으로 검증한다.

## Member versioning

Schema Set `1.2.0`의 18개 중 Dataset Manifest만
`rag-eval.dataset-manifest@1.3.0`으로 교체한다. 나머지 17개 member는 기존 member version과 canonical
bytes를 그대로 재사용한다. 다음 세 member를 `1.0.0`으로 추가한다.

- `rag-eval.authoring-identity-manifest@1.0.0`
- `rag-eval.index-build-receipt@1.0.0`
- `rag-eval.study-split-receipt@1.0.0`

따라서 전체 member는 경로와 schema ID가 각각 고유한 21개다. 기본 exporter version은 계속 `1.0.0`이며,
기존 `evals/schemas/1.0.0/`, `1.1.0/`, `1.2.0/`의 bytes와 hash는 변경하지 않는다.

## 적용 경계

Dataset Manifest `1.3.0`은 authoring identity manifest reference를 필수로 결속한다. 신규 세 계약은
authoring identity, 실제 Index build bridge, DEV/HOLDOUT split 검증 receipt의 구조를 각각 소유한다.
Loader 확장, 질문 본문, Dataset 상태 전이, 실제 Retrieval 실행과 성능 판정은 이 후보의 범위가 아니다.

책임 리뷰어의 Pull Request review event가 기록되기 전에는 이 문서 상태를 변경하거나, 이 후보를 #273
Dataset graph의 확정 입력으로 취급하지 않는다.
