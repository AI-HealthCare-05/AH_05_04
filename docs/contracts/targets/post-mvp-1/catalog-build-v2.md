# Catalog build·approval handoff v2 (#166 / PR #329)

상태: PR #329 변경 요청 반영 구현·검토 대상. 실제 DB adapter·Source/Catalog 승인 연동 및 Production 활성화는 미완료다. 이 문서는 현재 PR의 계산·인계 계약을 기술하며 외부 정본 Catalog projection hash를 대체한다고 선언하지 않는다.

## 근거와 범위

#329 RAG 변경 요청의 승인 자동 승격 제거, 독립 Ingredient 입력, 동일 제품 Alias dedupe, mapping 실패 응답 통일을 반영한다. 구현 담당은 김지혜, Candidate 계약 검토는 정현우, DB·승인 저장소 경계 검토는 송은영이다. 변경 결정 기록은 `docs/governance/decisions/2026-09-08-catalog-build-review-v2.md`를 따른다.

## 입력과 구성원

- Product·Ingredient는 각각 독립 입력이다. Ingredient는 `source_snapshot_id`, `source_record_key`, 공식 `code_system/canonical_code`, `ingredient_name`, `status`를 가진다.
- Component는 기존의 제품·성분 공식 코드 및 Snapshot 참조, role·order·strength를 전달한다. 성분 이름·원본 키·상태를 Component에서 받아 암묵적으로 Ingredient를 만들지 않는다.
- Ingredient 입력 생략은 빈 registry다. 이를 참조하는 Component는 실패한다. 동일 Ingredient는 여러 Product Component에서 재사용할 수 있다.
- 정확히 같은 행만 중복 제거한다. 같은 Identity의 상충 정의는 검증 실패로 유지한다.
- 같은 Product·normalized Alias를 여러 Source row/Snapshot에서 제공하면 Alias 구성원은 전부 보존한다. 적격 Alias에서 `(product_ref, normalized_text)`별 사전식으로 가장 작은 `alias_ref`를 가진 검색 항목 하나를 선택한다. 이는 출처 우선순위나 승인 우선순위가 아닌 결정적 대표 선택이다.
- 선택된 Entry는 해당 Alias의 Snapshot·출처를 유지한다. Product와 Alias Snapshot이 달라도 두 출처가 모두 Catalog source_refs에 있어야 한다.
- 같은 Alias reference의 상충 내용은 MEMBER_CONFLICT, 동일 정규화 Alias가 여러 활성 Product를 가리키면 ALIAS_CONFLICT다.
- Ingredient Alias는 제품 검색 항목으로 승격하지 않는다. 최종 Component DB 자연키는 별도 Source·Loader 근거 및 #164 인계 후 확정한다.

## 서비스 실패

존재하지 않는 Product·Ingredient·Alias 대상은 `REFERENTIAL_INTEGRITY_INVALID`를 가진 `REJECTED` 결과다. 잘못된 order나 필수 문자열 등 mapping 입력 오류는 기존 `MEMBER_CONFLICT`로 분류한다. 실패 상세는 안정 필드 경로만 포함하고 입력 원문을 담지 않는다. 구성원 검증 실패도 REJECTED로 반환한다. 이 경우 export와 저장 포트 호출은 없다.

승인 receipt의 내용·Source 결속 위반은 `CATALOG_APPROVAL_BINDING_INVALID`, manifest/typed export 불일치는 `CATALOG_MANIFEST_BINDING_INVALID`인 내부 인계 오류다. 성공 결과·저장을 반환하지 않는다. 승인 서비스 장애·repository 오류는 전파하고 성공으로 변환하지 않는다.

## 승인 경계

`CatalogBuildRequest`에서 승인 상태를 직접 받지 않는다. 승인된 서비스 조립 코드가 선택적으로 `CatalogApprovalVerifier`를 주입한다. 실제 verifier는 승인 저장소에서 권한·승인 주체·유효기간·회수 상태와 요청 범위를 검증해야 한다. 합성 verifier는 테스트 전용이며 운영 승인 증빙이 아니다.

검증 요청은 catalog_version, 실제 JSONL export_checksum, source_refs다. 결과 `CatalogApprovalReceipt`는 다음을 결속한다.

- receipt_id, catalog_version, export_checksum, Catalog verification_status, is_complete
- 각 source_ref(snapshot_id, source_version), Source receipt_id, verification_status, freshness_status

반환된 Source 목록은 중복 없이 요청 목록과 정확히 일치해야 한다. receipt는 생성한 JSONL checksum과 Catalog version에 결속돼야 한다. 이름만 같은 version이나 다른 Source subset을 승인한 receipt는 수용하지 않는다.

verifier 또는 receipt가 없으면 NOT_APPROVED·STALE로 내보낸다. STALE은 여기서 현재성 증명 부재에 대한 보수적 차단값이며 Source가 실제로 만료됐다고 판정하는 것은 아니다. 구성원이 검증되더라도 자동 승인하지 않는다.

Catalog·모든 Source가 APPROVED이고 모든 Source가 CURRENT이며, receipt가 완전성을 확인하고 검색 구성원이 존재할 때만 APPROVED다. 그 외에는 NOT_APPROVED다. source_refs·검증 포트 입력의 schema는 후속 normalization_run DB 인계를 대신하지 않는다.

## Manifest와 hash

schema_version은 `medication-catalog-v2`, canonicalization_spec_version은 `catalog-manifest-envelope-v2`다. 기존 v1의 승인 상태가 없는 manifest를 v2의 승인 증거로 재사용하지 않는다.

JSONL checksum은 UTF-8 canonical JSON 행을 기존 record 정렬 규칙으로 정렬하고 각 행 뒤 LF(마지막 행 포함)를 붙인 바이트의 SHA-256이다. 레코드 내용과 참조 규격은 build/export 코드를 따른다.

manifest hash는 catalog_manifest_hash 자신을 제외한 전체 manifest 객체를 `ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")`로 UTF-8 직렬화한 SHA-256이다. 의미 필드는 다음을 포함한다.

- canonicalization_spec_version, catalog_version, source_refs, normalization_version, schema_version
- declared_counts, export_checksum, duplicate_identity_count, orphan_count, conflict_count, validation_decision
- official_identity_code_systems, excluded_aliases
- verification_status, freshness_status, is_complete, approval_receipt(없으면 명시적 null)

source_refs는 Snapshot ID·Source version UTF-8 바이트순으로 정렬한다. receipt Source 목록은 Snapshot ID·version 문자열 사전순이다. 유효한 Unicode scalar 문자열에 대해 이 순서는 UTF-8 순서와 일치한다. JSON 객체 키는 Python sort_keys의 Unicode 순서를 사용한다. UTF-16 정렬 규칙인 Source 원문 checksum과 혼동하지 않는다.

`verify_catalog_export`는 hash 재계산, typed gate 상태·version·source_refs·counts, 실제 JSONL checksum 및 구성원 직렬화 일치를 확인한다. 서비스는 저장 전 이 검증을 수행한다. `build_candidate_index(artifacts, config, embedding_port)`는 `CatalogExportArtifacts` 전체를 받아 내부에서 매번 동일 검증을 수행한 뒤 typed Catalog를 사용한다. raw `CandidateCatalogExport`만 전달하거나 manifest·JSONL·gate 상태·구성원·검증 count 결속이 어긋나면 `CATALOG_MANIFEST_INVALID`로 실패하며 embedding을 호출하지 않는다. 내부 구성원 계산 함수는 외부 인계 API가 아니다. 이 무결성 검증은 승인 권한 검증이나 서명을 대신하지 않으며, 실제 승인 adapter 연결은 후속이다.

외부 정본의 Catalog projection hash와 본 envelope hash는 동등하다고 가정하지 않는다. Runtime 필드에 연결할 최종 hash 의미와 실제 승인 adapter는 후속 공유 계약에서 정렬한다. READY Set·DB normalization 실행·Runtime activation은 이번 변경에 포함하지 않는다.

## P0 Identity 허용 범위와 후속 리뷰

[현우님 추가 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/329#pullrequestreview-5138112718)에 따라 HIRA 접두사 차단을 명시적 P0 allowlist로 대체한다. 이번 구현안은 기존 합성 인계 계약의 Product `MFDS_ITEM_SEQ`, Ingredient `MFDS_INGREDIENT_CODE`만 허용한다. DB·Source 검토에서 확장하기 전에는 EDI·NHIS·HIRA 및 알 수 없는 체계를 허용하지 않는다.

Product/Ingredient 입력과 Component 양쪽 참조, Alias 대상은 엔티티별 허용 목록을 적용한다. 제외 대상은 lookup 전에 걸러 누락 대상을 조회하지 않으며 Product/Search Entry로 내보내지 않는다. Candidate 입력도 엔티티별 허용 목록을 검사한다. 공백·잘못된 입력 타입의 기존 validation은 유지한다. 보험 식별자를 제품 Identity로 변환하거나 서로 다른 Identity를 병합하지 않는다.

#323 병합 후 develop `2fa814a`를 반영했다. Source 코드·migration·문서·검증 기록은 병합된 develop과 일치시키고, #329 diff에는 Catalog·Candidate 변경만 남긴다. 단일 Alembic head는 `165f90716263`이다. 최종 push SHA의 CI를 확인한 뒤 재리뷰한다.
