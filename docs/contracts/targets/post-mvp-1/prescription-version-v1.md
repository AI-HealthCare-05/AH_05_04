# 처방 버전 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 |
| 구현·리뷰 | PR 1 DB foundation, PR 2 Version 1 Backfill·신규 생성 Dual-write, PR 3 Read cutover·정정 API 구현 · PR 4 Job·Outbox·Candidate 무효화와 Guide·Chat·Job 현재 노출 차단 구현, 지정 리뷰어 검토 대기 · PR 5 hardening 미구현 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md`, `track-b-adherence-v1.md`, `track-e-ocr-regression-v1.md`, `track-f-rag-citation-safety-v1.md` |
| Last verified | 2026-09-08 |

## 모델

`Prescription`은 논리적 처방 묶음이고 `PrescriptionVersion`은 불변 snapshot이다.

- `prescription`은 단 하나의 `active_version_id` FK를 활성 포인터로 가진다.
- `prescription_version`은 `prescription_id`와 `version_number`로 식별되는 불변 snapshot이다.
- `prescription_version_medication`은 version에 귀속된 확정 약물 snapshot이다.

`(prescription_id, version_number)`는 unique이고 `prescription.active_version_id`가 유일한 활성 포인터다. version row에 별도 `ACTIVE` 상태를 두지 않으므로 DB 제품별 partial unique 제약에 의존하지 않고도 활성 version을 하나로 표현한다. 활성화된 버전의 임상 입력 필드는 수정하지 않고 새 버전을 만든다.

소유권·출처·감사 시각을 위한 추가 물리 컬럼과 이름은 migration mapping, OpenAPI·DTO 및 관련 계약 테스트를 함께 제출하는 구현 PR에서 확정한다. 기준 문서가 고정하지 않은 컬럼을 이 계약에서 선행 확정하지 않는다.

### PR 1 물리 매핑

Revision `169a1b2c3d4e`는 API 동작을 변경하지 않는 Expand 단계다. Application ID/FK는 기존 스키마와 같은 `UUIDChar` 기반 `CHAR(36)`을 사용한다.

| 테이블 | 컬럼 |
| --- | --- |
| `prescription` | nullable `active_version_id` |
| `prescription_version` | `id`, `prescription_id`, `version_number`, `prescribed_date`, `confirmed_at`, internal `assembly_xid`, `created_at` |
| `prescription_version_medication` | `id`, `prescription_version_id`, `medication_name`, nullable `strength_text`, nullable `dose_value`, nullable `dose_unit`, nullable `frequency_per_day`, nullable `timing_text`, nullable `duration_days`, `display_order`, `created_at` |

`prescription.active_version_id`는 `(active_version_id, prescription.id) → prescription_version(id, prescription_id)` composite FK로 같은 처방의 version만 가리키게 한다. FK는 `DEFERRABLE INITIALLY DEFERRED`이므로 후속 NOT NULL 전환 뒤에도 미리 생성한 ID로 Prescription → Version → Medication을 같은 transaction에서 만들 수 있고 commit 시점에 완전한 graph를 검증한다. `active_version_id`는 기존 처방 Backfill 전까지 nullable이며 PR 2에서 version 1 생성·검증과 함께 채운다. 별도 active/current 상태 컬럼은 만들지 않는다.

Version sequence는 양수이고 `(prescription_id, version_number)`가 unique다. 약물 표시 순서는 양수이며 `(prescription_version_id, display_order)`가 unique다. 지연 제약은 commit 시 모든 Version과 active pointer에 medication snapshot이 1개 이상인지 확인한다. Version INSERT trigger는 caller 입력을 무시하고 DB가 발급한 epoch-aware top-level transaction ID를 internal `assembly_xid`에 기록한다. Medication INSERT는 현재 transaction ID가 이 값과 같은 Version에만 허용한다. 이 비교는 release된 SAVEPOINT 뒤에도 유지되며 Runtime 역할이 custom GUC나 INSERT 값으로 위조할 수 없으므로, active 여부와 관계없이 commit된 Version의 약물 집합은 동결된다. 두 snapshot 테이블은 DB trigger로 직접 UPDATE·DELETE를 차단한다.

사용자 데이터 삭제는 `prescription`을 삭제하는 기존 애플리케이션 경계에서만 시작한다. `prescription → prescription_version → prescription_version_medication` FK는 `ON DELETE CASCADE`이고, 불변성 trigger는 이 부모 연쇄 삭제만 허용한다. Version 또는 Version Medication 직접 삭제는 계속 차단한다. 지연 검증 trigger는 commit 전에 부모와 snapshot이 이미 연쇄 삭제된 경우 큐에 남은 생성·활성화 이벤트를 건너뛴다. Migration downgrade는 런타임 사용자 삭제와 별개이며 version data가 있으면 중단한다.

`profile`, `medical_document`, `ocr_job` 소유권·출처는 PR 1에서 중복 snapshot FK를 추가하지 않고 현재의 `prescription → profile`, `prescription → medical_document`, `prescription → ocr_job` 관계를 따른다. Candidate·Identification의 기존 문자열 FK 자리에는 아직 FK를 연결하지 않는다. Backfill되지 않은 현재 데이터와 API 호환성을 유지한 뒤 PR 3 Read cutover 범위에서 연결한다.

v2 이상은 같은 확정 처방 데이터에 대한 사용자 정정으로 생성하며 같은 문서를 새 OCR Job으로 재스캔·재확정하는 흐름은 PR 2/3 범위에 포함하지 않는다. 그런 흐름을 추가하려면 Version별 OCR provenance 필드와 계약을 별도로 승인한다.

### PR 2 Backfill·Dual-write 물리 매핑

Revision `169b2c3d4e5f`는 기존 `prescription`을 PK 오름차순 500건 단위로 잠그고 Version 1을 생성한다. `prescription`의 `prescribed_date`, `confirmed_at`, `created_at`을 Version header로 복사하고, 각 `medication`의 임상 입력 필드와 `display_order`, `created_at`을 `prescription_version_medication`에 그대로 복사한 뒤 `active_version_id`를 Version 1로 설정한다.

Backfill 전에는 다음 조건을 검사하며 하나라도 위반하면 전체 migration을 rollback한다.

- `prescription.profile_id = medical_document.profile_id`이고 문서 업로더가 해당 SELF Profile의 사용자일 것
- `source_ocr_job_id`가 같은 `medical_document`의 OCR Job일 것
- 모든 기존 Prescription에 Medication이 1개 이상 있을 것
- 모든 legacy Medication의 `medication_name`이 공백이 아닐 것
- Version row 유무와 `active_version_id` 설정 여부가 엇갈린 부분 graph가 없을 것

Backfill 뒤에는 active pointer 누락 0건, Version header 불일치 0건, legacy Medication과 active Version Medication의 양방향 `EXCEPT` 불일치 0건을 검증한다. Migration downgrade는 불변 감사 snapshot을 삭제하지 않는 no-op application rollback이다. 다시 upgrade하면 완성된 graph를 검증해 재사용하며 Version이나 Medication을 중복 생성하지 않는다.

신규 처방 확정은 기존 `prescription`·`medication`과 Version 1 snapshot을 같은 transaction에서 dual-write한다. Version ID를 먼저 생성해 `prescription.active_version_id`에 넣고 deferred composite FK 아래에서 Prescription → legacy Medication → Version → Version Medication을 원자 조립한다. 기존 read와 공개 API 응답은 계속 legacy `medication`을 사용한다. `active_version_id NOT NULL`, Version read cutover, Candidate·Identification·Guide·Chat FK 연결은 후속 PR 범위다.

PR 3의 cutover migration은 PR 2 완료 시점의 전체 Version coverage를 가정하지 않는다. 구버전 애플리케이션 rollback 등으로 새로 생긴 `active_version_id IS NULL` 처방이 있으면 PR 2와 같은 사전 검증·복사·완료 검증으로 Version 1을 먼저 재-backfill한다. Version row와 active pointer가 엇갈린 부분 graph는 추정 복구하지 않고 전체 migration을 중단한다.

그 뒤 Candidate·Identification cutover를 v2 생성 경로 공개 전에 실행한다. 현재 placeholder ID가 가리키는 legacy `medication`을 `(prescription_id, display_order)`로 같은 Prescription의 Version 1 Medication에 일대일 재매핑하고, 누락·중복·값 불일치가 0건임을 검증한 뒤에만 실제 FK를 추가한다. 검증할 수 없는 기존 행이 하나라도 있으면 추정 연결하거나 삭제하지 않고 migration 전체를 중단한다. 이 재-backfill·재매핑과 FK 적용이 끝날 때까지 정정 API와 Candidate/RAG publication gate는 닫아 둔다. P0에서 Version 간 안정적 Medication 계보 Key를 새로 도입하지 않는 기존 Decision은 유지한다.

## 활성화

활성 여부는 `prescription.active_version_id`와의 일치로 판정한다. 활성화 transaction은 Prescription row를 잠근 뒤 `active_version_id`를 새 불변 version으로 갱신한다. 이전 version은 불변 이력으로 남고 활성 여부를 나타내는 중복 상태 컬럼을 유지하지 않는다.

OCR 검수 완료만으로 자동 활성화하지 않는다. 사용자의 명시적 처방 확정 동작이 필요하다.

활성화 대상 version에는 확정 medication snapshot이 1개 이상 있어야 한다. 없으면 `422 PRESCRIPTION_MEDICATION_REQUIRED`를 반환하고 활성화 transaction 전체를 rollback한다.

새 version 활성화 transaction은 다음 변경을 원자적으로 수행한다.

1. 이전 version의 `PENDING`, `PROCESSING`, `RETRY_WAIT` Job을 모두 `STALE`로 전환한다.
2. 해당 Job의 미발행·예약 Outbox를 `CANCELLED` 처리한다.
3. Track B의 동기 port로 effective 시각 이후의 이전 version `PENDING` occurrence와 미전달 알림을 취소한다.
4. `prescription.active_version_id`를 새 version으로 변경한다.

이미 실행 중인 Provider 호출의 강제 취소에는 의존하지 않는다. Worker가 결과를 commit할 때 active version과 lease를 다시 확인하고, 둘 중 하나라도 유효하지 않으면 현재 결과로 공개하지 않는다. 이전 완료 결과는 삭제하지 않지만 현재 결과, 새 Chat 문맥과 향후 일정에는 사용하지 않으며 직접 URL 조회에서도 active version을 검증한다.

## 하위 데이터 귀속

다음 데이터는 생성 당시 `prescription_version_id`를 반드시 저장한다.

- 복약 가이드와 Chat 세션
- 복약 일정과 occurrence
- 공식 Candidate Search·append-only Identification과 Identification Preflight 실행 기록
- Safety Result와 인용
- OTC 질문을 포함한 Guide·Chat Rule-first RAG 결과
- OCR 이후 확정된 처방 입력에서 파생된 Job

처리 중 Job의 version이 더 이상 active가 아니면 결과는 보존하되 Job을 `STALE`로 종결하고 현재 화면에 자동 반영하지 않는다.

## 기존 데이터 마이그레이션

1. 기존 prescription마다 version 1 row를 생성한다.
2. 기존 확정 약물을 version 1 medication snapshot으로 복사한다.
3. 기존 prescription의 `active_version_id`를 version 1로 설정한다.
4. 하위 레코드의 version 1 FK는 각 소비 도메인의 cutover migration에서 backfill한다.
5. 검증 쿼리로 orphan, 중복 version number, 유효하지 않은 `active_version_id`가 없음을 확인한다.
6. 검증 후에만 새 FK와 NOT NULL 제약을 활성화한다.

마이그레이션은 원본 row를 삭제하지 않으며 다음 runbook으로 수행한다.

1. **Expand:** version 테이블과 nullable version FK를 추가하고 기존 컬럼을 유지한다. PR 1에서 완료했다.
2. **Dual-write:** Expand schema 위에 새 writer를 먼저 배포한다. 새 쓰기는 legacy row와 Version 1 snapshot을 함께 채우고 read는 계속 legacy 구조를 사용한다.
3. **Backfill:** dual-write 동작을 확인한 뒤 처방 PK 범위별 재실행 가능한 batch로 기존 처방의 Version 1과 Medication snapshot을 만든다. writer를 중지하고 migration하는 배포에서는 같은 release의 dual-write 코드만 재시작하며 구 writer를 다시 띄우지 않는다.
4. **Verify:** orphan 0건, version number 중복 0건, 유효하지 않은 active pointer 0건, snapshot 수와 핵심 값 일치를 검증한다.
5. **Read cutover:** 먼저 누락 Version을 방어적으로 재-backfill하고 검증한 뒤 새 구조 read로 전환한다. 한 배포 구간을 관찰한 후 소비 FK·unique·NOT NULL 제약을 활성화한다.
6. **Rollback:** read cutover 전에는 legacy read로 application rollback할 수 있지만 dual-write보다 이전 writer로 rollback하지 않는다. 불가피하게 구 writer가 실행됐으면 쓰기를 중지하고 cutover 전에 누락분을 재-backfill한다. Version row를 삭제하는 downgrade는 금지하고 forward-fix한다.

테이블·컬럼별 mapping, batch 크기와 검증 SQL은 migration PR의 필수 산출물이다.

## 동시 수정

새 버전 생성 요청은 현재 `active_version_id`를 `If-Match` 또는 body의 `base_version_id`로 전달한다. 값이 달라졌으면 `409 PRESCRIPTION_VERSION_CONFLICT`를 반환한다.

### 처방 정정 API와 DTO

`PATCH /api/v1/prescriptions/{prescription_id}`는 사용자가 확인한 처방 전체 snapshot을 새 Version으로 저장한다. 부분 약물 patch는 허용하지 않는다. 요청 body는 다음 필드로 고정한다.

- `base_version_id`: 조회 응답의 현재 `prescription_version_id`
- `expected_revision`: 조회 응답의 현재 양수 `revision`
- `prescribed_date`
- 비어 있지 않은 `medications[]`: `medication_name`, nullable `strength_text`, nullable `dose_value`, nullable `dose_unit`, nullable `frequency_per_day`, nullable `timing_text`, nullable `duration_days`, 양수이면서 요청 안에서 unique인 `display_order`

서버는 `prescription`을 먼저 잠그고 `base_version_id == active_version_id`와 `expected_revision == active version.version_number`를 모두 검증한다. 둘 중 하나라도 다르면 mutation 없이 `409 PRESCRIPTION_VERSION_CONFLICT`다. 성공하면 다음 `version_number`의 불변 `prescription_version`과 모든 `prescription_version_medication`을 같은 transaction에서 만들고 active pointer를 바꾼 뒤 `200`으로 새 snapshot을 반환한다.

처방 확정·상세·최신·정정 응답은 `prescription_id`, 실제 `prescription_version_id`, 양수 `revision`, `current`, `document_id`, Version의 `prescribed_date`·`confirmed_at`, `medications[]`를 반환한다. 각 약물에는 실제 `prescription_version_medication_id`와 snapshot 임상 필드가 포함된다. `current`는 반환 Version과 응답 시점 `active_version_id`의 일치 여부다.

PR 3은 Version read와 Candidate·Identification·Guide·Chat·Guide Job의 생성 시점 Version 귀속까지 전환했다. PR 4는 Version 변경 transaction에서 이전 Version의 미종료 Job·Outbox·Candidate Search를 무효화하고 Guide·Chat·Job 결과의 현재 노출을 차단한다. 따라서 PR 3의 임시 `PRESCRIPTION_CORRECTION_ENABLED` gate를 제거하고 정정 route를 공개한다. legacy `prescription_id`·`medication` dual-write 제거 및 nullable 정리는 PR 5 범위다.

Candidate Search snapshot의 `medication_name_snapshot`·`strength_text_snapshot`은 FK 대상 PVM 값과 정확히 일치해야 한다. cutover migration은 불일치가 한 건이라도 있으면 FK 적용 전에 전체를 중단한다. Candidate Search·Result·Identification은 감사 이력이므로 Prescription/PV/PVM 삭제에 연쇄 삭제되지 않으며 참조가 남아 있으면 삭제를 거부한다.

Cutover 이후 Candidate/Identification의 legacy ID remap이나 Guide/Chat의 Version provenance가 하나라도 존재하면 schema downgrade는 데이터 유실 없이 되돌릴 수 없으므로 거부한다. 배포 시 writer를 먼저 중지한 상태에서 migration을 수행하며, 실패 시 이전 writer로 downgrade하지 않고 같은 schema에서 application rollback 또는 forward-fix한다.

처방 활성화와 Job 처리의 전역 lock 순서는 `PRESCRIPTION → CHAT_SESSION(해당 시) → AI_JOB → 도메인 row → OUTBOX`다. 각 transaction은 필요한 row만 이 순서로 잠그며 역순 잠금을 금지한다.

처방 version 활성화 write service가 transaction owner다. 이전 version의 미래 일정 정리가 필요하면 Track B의 동기 `cancel_future_for_prescription_version` port를 AI Job 상태 전이 뒤 도메인 row 단계에서 같은 transaction 안에 호출한다. effective 시각 이후의 이전 version `PENDING` occurrence와 미전달 알림만 취소하며, 과거 기록을 삭제하거나 새 version에 재귀속하지 않는다. 이 경계에 비동기 event나 사후 보상을 사용하지 않는다.

PR 4 구현은 처방 row 잠금 뒤 이전 Version의 `PENDING | PROCESSING | RETRY_WAIT` Job을 `STALE`로
종결하고 실행 중 Attempt를 `BLOCKED`로 보존한다. 연결된 `PENDING | CLAIMED` Outbox는
`CANCELLED`로 전환하며 이미 `PUBLISHED`인 event는 변경하지 않는다. Worker lease를 제거하고
마지막 event를 consumed provenance로 보존하므로 실행 중 결과 commit은 fencing에서 탈락한다.
이전 Version의 `RUNNING | READY` Candidate Search만 `INVALIDATED_INPUT_CHANGED`로 바꾸고,
완료·소비·거절·만료 Search와 Identification 감사 이력은 변경하지 않는다.

Guide·Chat의 ID 직접 조회·메시지 전송은 저장된 Version과 현재 active Version이 다르면
`409 PRESCRIPTION_VERSION_CONFLICT`로 거부한다. 처방 기준 최신 Guide·Chat 조회는 active Version
결과만 선택한다. 완료된 이전 Version Job은 상태와 provenance를 계속 조회할 수 있지만
`result_url`을 반환하지 않는다.

현재 동기 Chat 메시지 전송은 동일 `CHAT_SESSION` row lock으로 같은 세션 요청만 직렬화한다. 서로 다른
세션의 Provider 호출은 병렬로 수행하며 그 구간에는 `PRESCRIPTION` row lock을 유지하지 않는다. 결과
commit 직전에 생성 기준 Version과 `active_version_id`의 일치를 `PRESCRIPTION` row lock으로 다시
검증한다. Provider 호출 중 정정이 commit되어 불일치하면 결과 content·model·prompt metadata를 저장하지
않고 ASSISTANT placeholder를 `FAILED / PRESCRIPTION_VERSION_STALE`로 보존하며
`409 PRESCRIPTION_VERSION_CONFLICT`를 반환한다. 처방 활성화 transaction은 기존 `CHAT_SESSION` row를
잠그지 않으므로 이 완료 fencing과 역방향 lock cycle을 만들지 않는다.
