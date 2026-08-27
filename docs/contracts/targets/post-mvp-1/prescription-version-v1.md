# 처방 버전 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 |
| 구현·리뷰 | Not implemented · 구현 동기화와 관련 지정 리뷰어 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md`, `track-b-adherence-v1.md`, `track-e-ocr-regression-v1.md`, `track-f-rag-citation-safety-v1.md` |
| Last verified | 2026-08-27 |

## 모델

`Prescription`은 논리적 처방 묶음이고 `PrescriptionVersion`은 불변 snapshot이다.

- `prescription`은 단 하나의 `active_version_id` FK를 활성 포인터로 가진다.
- `prescription_version`은 `prescription_id`와 `version_number`로 식별되는 불변 snapshot이다.
- `prescription_version_medication`은 version에 귀속된 확정 약물 snapshot이다.

`(prescription_id, version_number)`는 unique이고 `prescription.active_version_id`가 유일한 활성 포인터다. version row에 별도 `ACTIVE` 상태를 두지 않으므로 DB 제품별 partial unique 제약에 의존하지 않고도 활성 version을 하나로 표현한다. 활성화된 버전의 임상 입력 필드는 수정하지 않고 새 버전을 만든다.

소유권·출처·감사 시각을 위한 추가 물리 컬럼과 이름은 migration mapping, OpenAPI·DTO 및 관련 계약 테스트를 함께 제출하는 구현 PR에서 확정한다. 기준 문서가 고정하지 않은 컬럼을 이 계약에서 선행 확정하지 않는다.

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
4. 하위 레코드에 version 1 FK를 backfill한다.
5. 검증 쿼리로 orphan, 중복 version number, 유효하지 않은 `active_version_id`가 없음을 확인한다.
6. 검증 후에만 새 FK와 NOT NULL 제약을 활성화한다.

마이그레이션은 원본 row를 삭제하지 않으며 다음 runbook으로 수행한다.

1. **Expand:** version 테이블과 nullable version FK를 추가하고 기존 컬럼을 유지한다.
2. **Backfill:** 처방 PK 범위별 재실행 가능한 batch로 version 1과 medication snapshot을 만들고 하위 FK를 채운다.
3. **Dual compatibility:** 새 쓰기는 version snapshot과 구버전 read에 필요한 필드를 함께 채운다. 읽기는 version FK가 있으면 새 구조를 우선하고 없으면 기존 구조로 fallback한다.
4. **Verify:** orphan 0건, version number 중복 0건, 유효하지 않은 active pointer 0건, snapshot 수와 핵심 값 일치를 검증한다.
5. **Cutover:** 새 구조 read로 전환한 뒤 한 배포 구간을 관찰하고 FK·unique·NOT NULL 제약을 활성화한다.
6. **Rollback:** contract 전에는 구버전 read로 application rollback할 수 있다. contract 뒤에는 version row를 삭제하는 downgrade를 금지하고 forward-fix한다.

테이블·컬럼별 mapping, batch 크기와 검증 SQL은 migration PR의 필수 산출물이다.

## 동시 수정

새 버전 생성 요청은 현재 `active_version_id`를 `If-Match` 또는 body의 `base_version_id`로 전달한다. 값이 달라졌으면 `409 PRESCRIPTION_VERSION_CONFLICT`를 반환한다.

처방 활성화와 Job 처리의 전역 lock 순서는 `PRESCRIPTION → CHAT_SESSION(해당 시) → AI_JOB → 도메인 row → OUTBOX`다. 각 transaction은 필요한 row만 이 순서로 잠그며 역순 잠금을 금지한다.

처방 version 활성화 write service가 transaction owner다. 이전 version의 미래 일정 정리가 필요하면 Track B의 동기 `cancel_future_for_prescription_version` port를 AI Job 상태 전이 뒤 도메인 row 단계에서 같은 transaction 안에 호출한다. effective 시각 이후의 이전 version `PENDING` occurrence와 미전달 알림만 취소하며, 과거 기록을 삭제하거나 새 version에 재귀속하지 않는다. 이 경계에 비동기 event나 사후 보상을 사용하지 않는다.
