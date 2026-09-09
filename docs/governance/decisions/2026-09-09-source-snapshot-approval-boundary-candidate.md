# Product Decision Candidate: Source Snapshot 승인·거부 경계

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-362-20260909` |
| 상태 | Review pending · Issue #362 |
| 구현 | 김지혜 (`@Jye-rookie`) |
| 책임 리뷰 | 송은영 (`@phina-io`) — Source ingestion·DB |
| 선행 Decision | [Source Snapshot DB 상태 전이](./2026-09-08-source-snapshot-db-transition.md) |
| 의미 계약 검토 | 정현우 (`@ceohwj`) — Catalog·Runtime Source 사용 |
| 추적 Issue | [#362](https://github.com/AI-HealthCare-05/AH_05_04/issues/362) |

## 목적

Production `source_version` 생성·검증, 외부 Version 결속, Snapshot 승인·거부와
Catalog·Runtime 사용 가능 조건을 하나의 fail-closed 계약으로 고정한다.

이 Decision은 Freshness 계산 로직 자체, Catalog hash 계약과 Runtime Retrieval 구현을 변경하지 않는다.

## Source version 계약

Production Source producer는 다음 형식 중 하나만 생성한다.

| Source 조건 | `source_version` | `external_version` |
| --- | --- | --- |
| 제공자 불변 Version 존재 | `external:<external_version>` | 동일 값을 byte-for-byte 보존 |
| 외부 Version 없는 API | `api:<UTC RFC3339 6자리 소수초>:<canonical_checksum>` | `NULL` |
| 승인 내부 Fixture | `internal:<불변 fixture_version>:<canonical_checksum>` | `NULL` |

이 Candidate는 Application과 정규 계약의 `source_version` 최대 길이를
200자로 채택하는 안을 제시한다. 승인 전에는 확정 Decision으로 해석하지 않으며,
PR 병합 전에 책임 리뷰 승인이 필요하다.

현재 `rag_source_snapshot.source_version`과 #369의
`rag_citation.source_version`은 `VARCHAR(255)`다. Candidate 승인 후 #369가
병합된 최신 Alembic head에서 두 컬럼을 200자로 정렬하고 DB CHECK를 추가한다.
해당 migration이 완료되기 전에는 Application 검증이 DB보다 엄격한 상태다.

공통 조건:

- 전체 길이는 1~200자이며 NFC 문자열이다.
- 공백과 제어문자를 허용하지 않는다.
- `external:` payload는 최대 191자다.
- API·Internal checksum suffix는 해당 Snapshot의 `canonical_checksum`과 일치해야 한다.
- Internal `fixture_version`은 이동 가능한 이름만으로 만들지 않고 승인 Commit과 Fixture Manifest에서 재현할 수 있어야 한다.
- 형식이나 결속 검증에 실패하면 Snapshot을 생성하지 않는다.

`validate_source_version()`이 반환한 `SourceVersionKind`를 검증 결과의 정본으로
사용한다. 후속 소비자는 `source_version`의 부분 문자열이나 중첩 접두사를 다시
파싱해 kind를 추정하지 않는다. `external:` payload가 `external:`, `api:`,
`internal:` 예약 접두사로 시작하면 거부한다.

`SnapshotIngestionMetadata.external_version`은 현재 생성·결속 검증에만 사용하며
DB에는 저장되지 않는다. #369 이후 migration에서 저장 컬럼과 Receipt 조회 경로가
연결되기 전까지 후속 소비자는 이 값이 영속 보존된다고 가정하지 않는다.

## 동일 Version 충돌과 NO_CHANGE

| 상황 | 결과 |
| --- | --- |
| 같은 `source_version`과 같은 canonical contract 재시도 | 기존 Snapshot을 가리키는 `NO_CHANGE` |
| 새 `source_version`, canonical contract 동일 | 새 Snapshot을 만들지 않는 기존 `NO_CHANGE` 계약 유지 |
| 같은 `source_version`, 다른 canonical contract | `SOURCE_VERSION_CONFLICT`, Snapshot 생성 금지 |
| 내용이 `A → B → A`로 복귀하고 새 `source_version`이 생성됨 | 새 Snapshot과 계보 보존 |

`SOURCE_VERSION_CONFLICT`는 이미 관측한 동일 Version의 canonical contract가 달라진 사건에만 사용한다.
Producer가 잘못된 문법이나 checksum suffix를 생성한 오류에 재사용하지 않는다.

## 승인·거부 정책

`source-ingestion-integrity=PASSED`는 Parser·checksum·Artifact 결속 통과이며
사람의 publication 승인을 뜻하지 않는다.

| 수집 결과 | Run | Snapshot | Catalog·Runtime 사용 |
| --- | --- | --- | --- |
| 정상 결과, 거부 0건 | `SUCCEEDED` | `PENDING` 후보 생성 | 승인·Freshness 조건 충족 전 금지 |
| 거부가 두 Hard Limit 이하 | `SUCCEEDED_WITH_REJECTIONS` | `PENDING` 후보 생성 | 별도 publication 승인 전 금지 |
| 거부 건수 또는 비율 Hard Limit 초과 | `FAILED` | 생성하지 않음 | 금지 |
| 빈 결과와 `empty_result_policy=REJECT` | `FAILED/EMPTY_RESULT` | 생성하지 않음 | 금지 |
| Schema Drift | `FAILED/SCHEMA_DRIFT` | 생성하지 않음 | 금지 |
| Source version 생성·결속 실패 | `FAILED` | 생성하지 않음 | 금지 |

두 거부 Hard Limit은 자동 승인 허용치가 아니다. 제한 이하의 거부도 사람 승인 없이 공개하지 않는다.

## Freshness와 저장 상태

Snapshot의 `PENDING`, `CURRENT`, `STALE`, `FAILED` 상태 전이와 DB 소유권 규칙은
[Source Snapshot DB 상태 전이 Decision](./2026-09-08-source-snapshot-db-transition.md)을
따른다. 이 Candidate는 해당 전이를 변경하지 않고, 승인된 Snapshot의 Freshness 정본
해석과 Catalog·Runtime 사용 가능 조건만 정의한다.

Snapshot Freshness의 정본은 승인된 Freshness Policy와 평가 시점으로 계산한 파생 결과다.
기존 `verification_status=CURRENT|STALE`를 Freshness 자체의 정본으로 사용하지 않는다.

기존 컬럼·부분 Unique Index·상태 전이 함수의 물리 변경은 #164 공유 Schema와
현재 데이터 영향을 확인한 migration에서 처리한다. #362 구현 중 컬럼을 먼저 삭제하거나
기존 Snapshot을 임의 변환하지 않는다.

## Fail-closed reason code 제안

Catalog·Runtime이 Snapshot을 사용할 수 없을 때 아래 내부 reason을 기록한다.

| Reason | 의미 |
| --- | --- |
| `SOURCE_VERSION_INVALID` | Production 문법·NFC·길이 검증 실패 |
| `SOURCE_VERSION_BINDING_MISMATCH` | 외부 Version 또는 checksum 결속 불일치 |
| `SOURCE_VERSION_CONFLICT` | 동일 Version의 canonical contract 충돌 |
| `SNAPSHOT_NOT_APPROVED` | 승인 결정이 없거나 검수 대기 |
| `SNAPSHOT_REJECTED` | 최신 Snapshot 검토 결과가 거부 |
| `SNAPSHOT_FRESHNESS_STALE` | 승인 Freshness Policy 기준 사용 불가 |
| `SNAPSHOT_PROVENANCE_INVALID` | Snapshot의 version/hash/Receipt 결속 누락·불일치 |

이 reason은 내부 감사·차단 근거다. Runtime Retrieval에서 발견한 저장 provenance 불일치는
`SOURCE_VERSION_CONFLICT`로 바꾸지 않고 기존 계약대로 `VALIDATION_ERROR/VALIDATION_FAILED`로 닫는다.

## 후속 검증 Receipt

후속 Catalog·Runtime은 최소한 다음 값을 exact-match한다.

- `source_id`, `source_code`
- `endpoint_id`, nullable `operation_id`
- `source_snapshot_id`
- `source_version`, nullable `external_version`
- `canonical_checksum`
- `canonicalization_spec_version`
- `endpoint_receipt_hash`
- Snapshot 승인 Decision 또는 Verification 참조
- 적용한 Freshness Policy Version과 평가 시각
- 판정 결과와 nullable fail-closed reason code

Secret, 전체 인증 URL, Query String과 실응답 원문은 Receipt에 포함하지 않는다.

## 구현 순서

1. Production `source_version` 생성·검증
2. 실패 코드와 실패 Run 저장 연결
3. Source별 거부·빈 결과 정책 적용
4. Snapshot 승인·사용 가능 판정 구현
5. #369 병합 후 최신 Alembic head에서 DB 컬럼·제약 연결
6. Catalog·Runtime 소비자 계약 테스트와 Receipt 검증
