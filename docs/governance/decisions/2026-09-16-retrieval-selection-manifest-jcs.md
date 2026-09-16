# Decision: Retrieval Selection Manifest RFC 8785 JCS Alignment 및 Receipt 2.0 컷오버

- **문서 ID**: `PD-178-20260916`
- **일자**: 2026-09-16
- **상태**: Approved / Implemented in branch (`fix/178-retrieval-selection-manifest-jcs`)
- **구현 담당자**: 정현우 (`@ceohwj`, AI/RAG)
- **단일 책임 리뷰어**: 송은영 (`@phina-io`, Backend / Data & Security Technical Controls)
- **교차 영향 도메인**: 권가빈 (`@hazelnutflavoured`, PM / Track C 책임자), 김지혜 (`@Jye-rookie`, Worker / Source Provenance)
- **관련 Issue / PR**: Part of #178, Prerequisite for #180, Unblocks PR #623 marker

---

## 1. 배경 및 문제 제기

PR #623(`docs/governance/decisions/2026-09-15-guide-evidence-handoff.md`)에서 선행 구현된 Guide Evidence Handoff 커널은 RFC 8785 JSON Canonicalization Scheme (JCS) 표준을 엄격히 적용하였으나, #178의 `ai_worker/tasks/rag/retrieval_run.py` 내 `canonical_json_bytes`는 `json.dumps(..., sort_keys=True)`를 사용하여 Unicode code point 순으로 정렬하는 비표준 구현에 머물러 있었다.

이로 인해:
1. Non-BMP 키 정렬 등에서 RFC 8785 JCS(UTF-16 code unit 정렬)와의 잠재적 직렬화 불일치 위험이 상존했다.
2. `ProductionSearchReceipt`가 소비하는 `compute_selection_manifest_hash`의 preimage가 6개 필드만 포함하는 축약 투영에 불과하여, `PD-315` 및 #180 선행 계약에서 요구하는 17개 출처 및 정규화 메타데이터를 충족하지 못했다.
3. PR #623에 `BLOCKED_BY_178_CANONICAL_HASH_CONTRACT` 비강제 marker가 기록되어 #180 런타임 통합의 blocking 요인으로 작용하고 있었다.

---

## 2. 핵심 결정 사항

### 2.1 RFC 8785 표준 JCS 단일 공용 모듈 위임
- `ai_worker/tasks/rag/retrieval_run.py`의 `canonical_json_bytes` 및 `sha256_canonical_json` 구현을 표준 RFC 8785 모듈인 `ai_worker.tasks.evaluation.canonical`로 전면 위임한다.
- `ai_worker/tasks/rag/guide_evidence_handoff.py`에 중복 정의되어 있던 자체 JCS 직렬화 코드(`_validate_jcs_string`, `_validated_jcs_value`, `_order_jcs_objects`, `canonical_jcs_bytes`, `canonical_jcs_sha256`)를 제거하고 공용 모듈의 `canonical_json_bytes`, `canonical_sha256`으로 통합한다.

### 2.2 기영속 Run 및 Manifest 하위 호환성 (Golden Regression)
- 기존 영속화된 Run, Signal, Hit 매니페스트는 영문 ASCII 키만 사용하므로 UTF-16 code unit 정렬과 Python code point 정렬의 결과가 100% 일치한다.
- 변경 전 산출된 legacy golden digest(Signal, Hit, Receipt)를 상수로 동결하고 회귀 테스트(`test_legacy_digest_golden_regression_frozen_constants`, `test_legacy_persisted_retrieval_run_replay_compatibility`)로 불변성을 영구 보장한다.

### 2.3 Retrieval Selection Manifest v2 프로젝션 스키마 확정
- 프로젝션 버전 식별자: `"retrieval-selection-manifest-v2"`
- 반환 형식: `JsonValue`
- `final_rank`는 `ProductionSearchHit.fusion_rank`로부터 엄격하게 투영 및 정렬된다.
- 선택 항목 투영 17개 필드:
  1. `canonical_checksum` (`str`)
  2. `canonicalization_spec_version` (`str`)
  3. `chunk_index` (`int`)
  4. `content_sha256` (`str`, from `h.provenance.content_hash`)
  5. `external_document_id` (`str`)
  6. `final_rank` (`int`, from `h.fusion_rank`)
  7. `index_code` (`str`)
  8. `index_configuration_hash` (`str`)
  9. `index_version` (`str`)
  10. `knowledge_chunk_id` (`str`)
  11. `knowledge_index_id` (`str`)
  12. `locator` (`str`)
  13. `normalization_version` (`str`)
  14. `source_code` (`str`)
  15. `source_snapshot_id` (`str`)
  16. `source_snapshot_member_id` (`str`)
  17. `source_version` (`str`)

### 2.4 불변식 검증 및 Fail-Closed 원칙
- `selection_manifest_projection` 함수는 `h.fusion_rank <= 0`이거나 selection 내 중복 `fusion_rank`가 존재하는 경우 `ValueError`를 발생시켜 fail-closed 처리한다.
- `guide_evidence_handoff`는 manifest 재계산 시 발생하는 `ValueError`를 포획하여 `SELECTION_MANIFEST_MISMATCH` 거부 사유로 fail-closed 처리한다.

### 2.5 ProductionSearchReceipt 2.0 컷오버 및 v1 영구 거부
- `compute_production_search_receipt`의 발급 버전을 `"2.0"`으로 컷오버한다.
- `guide_evidence_handoff`의 `_receipt_structure_is_valid`는 `receipt.artifact_ref.artifact_code == "production_search_receipt"` 및 `receipt.artifact_ref.version == "2.0"`을 필수 구조 불변식으로 검증한다.
- 버전 `"1.0"` 영수증이 유입되는 경우 `RETRIEVAL_RECEIPT_MISMATCH`로 즉시 거부한다.

### 2.6 의존성 Marker 해소 및 범위 제한
- `BLOCKED_BY_178_CANONICAL_HASH_CONTRACT` marker를 코드 및 계약 문서에서 완전히 제거한다.
- Downstream의 `BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT` marker는 이번 PR의 범위가 아니므로 그대로 유지한다.
- DB 스키마, 마이그레이션, LangGraph 런타임 오케스트레이션, `PUBLIC_TRACK_F=false` 공개 플래그는 본 변경의 범위 밖이며 일체 변경하지 않는다.

---

## 3. 검증 결과

1. **JCS 표준성 및 Non-BMP 키 정렬 회귀 검증**: `test_retrieval_run_canonical_json_bytes_conforms_to_rfc8785_utf16_ordering` 통과.
2. **동결 레거시 매니페스트 호환성 검증**: 동결 상수 `FROZEN_LEGACY_SIGNAL_HASH`, `FROZEN_LEGACY_HIT_HASH`, `FROZEN_LEGACY_RECEIPT_HASH` 일치 확인.
3. **Selection Manifest v2 프로젝션 및 불변식 검증**: 순위 민감도, 출처 필드 민감도, 비정상 rank fail-closed 검증 통과.
4. **Receipt 2.0 컷오버 및 Handoff 결속 검증**: v1 영수증 거부(`RETRIEVAL_RECEIPT_MISMATCH`) 및 #178-#623 해시 일치 검증 통과.
5. **기존 단위 테스트 스위트 회귀 검증**:
   - `ai_worker/tests/rag`: 1911 passed.
   - `ai_worker/tests/evaluation`: 1422 passed.
