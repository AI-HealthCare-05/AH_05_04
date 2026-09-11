# Product Decision: Source Snapshot 승인·거부 경계

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-362-20260909` |
| 상태 | Approved |
| 구현 | 김지혜 (`@Jye-rookie`) |
| 책임 리뷰 | 송은영 (`@phina-io`) — Source ingestion·DB |
| 상위 Decision | [PD-315 Production Evidence Retrieval 계약](./2026-09-08-production-evidence-retrieval-contract-divergence.md) · #361 merged |
| 선행 Decision | [Source Snapshot DB 상태 전이](./2026-09-08-source-snapshot-db-transition.md) |
| 의미 계약 검토 | 정현우 (`@ceohwj`) — Catalog·Runtime Source 사용 |
| 추적 Issue | [#362](https://github.com/AI-HealthCare-05/AH_05_04/issues/362) |

## 목적

Production `source_version` 생성·검증, 외부 Version 결속, Snapshot 승인·거부와
Catalog·Runtime 사용 가능 조건을 하나의 fail-closed 계약으로 고정한다.

`external/api/internal` 문법과 전체 200자·`external:` payload 191자 상한은
PD-315/#361이 정한 상위 계약을 따른다. 이 Decision은 값을 다시 결정하지 않고
Source producer 검증, `external_version` 영속화, 거부·publication·Freshness 저장
경계를 구체화한다.

이 Decision은 Freshness 계산 로직 자체, Catalog hash 계약과 Runtime Retrieval 구현을 변경하지 않는다.

## Source version 계약

Production Source producer는 다음 형식 중 하나만 생성한다.

| Source 조건 | `source_version` | `external_version` |
| --- | --- | --- |
| 제공자 불변 Version 존재 | `external:<external_version>` | 동일 값을 byte-for-byte 보존 |
| 외부 Version 없는 API | `api:<UTC RFC3339 6자리 소수초>:<canonical_checksum>` | `NULL` |
| 승인 내부 Fixture | `internal:commit-<40 lowercase hex>-manifest-<64 lowercase hex>:<canonical_checksum>` | `NULL` |

Application은 PD-315/#361의 정규 `source_version` 최대 길이 200자를 적용한다.
이 Decision의 거부·publication·저장 경계는 #362 구현과 후속 소비자가 따르는
확정 계약이다.

현재 `rag_source_snapshot.source_version`과 #369의
`rag_citation.source_version`은 `VARCHAR(255)`다. #369가
병합된 최신 Alembic head에서 두 컬럼을 200자로 정렬하고 DB CHECK를 추가한다.
해당 migration이 완료되기 전에는 Application 검증이 DB보다 엄격한 상태다.

공통 조건:

- 전체 길이는 1~200자이며 NFC 문자열이다.
- 공백과 제어문자를 허용하지 않는다.
- `external:` payload는 최대 191자다.
- API·Internal checksum suffix는 해당 Snapshot의 `canonical_checksum`과 일치해야 한다.
- Internal `fixture_version`은 `commit-<40 lowercase hex>-manifest-<64 lowercase hex>` 형식이다. `latest`, branch, tag와 같은 이동 가능한 이름을 허용하지 않으며 Commit과 Fixture Manifest를 함께 exact-bind한다.
- 형식이나 결속 검증에 실패하면 Snapshot을 생성하지 않는다.

이 규칙은 기존 공유 Target과 PD-315에 있던 승인 Git tag 허용 경로를 제거하는 계약
변경이다. tag는 검토 metadata로는 기록할 수 있지만 `source_version` 식별자로 사용하지
않는다. 공유 Target과 PD-315도 이 Decision과 함께 commit·manifest exact binding으로
정렬한다.

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

## Snapshot과 수집 시도 provenance 분리

Snapshot provenance와 Ingestion Run/Attempt provenance는 서로 다른 사실을 보존한다.

| 저장 축 | 보존하는 사실 |
| --- | --- |
| Snapshot | 실제 생성된 Snapshot의 `source_version`, nullable `external_version`, canonical contract |
| Ingestion Run/Attempt | 매 실행이 시도한 `source_version`, nullable `external_version`, 비교한 canonical contract와 판정 |

새 `source_version`의 canonical contract가 기존 Snapshot과 같아 `NO_CHANGE`가 되더라도
Run/Attempt에는 새 version 관측 사실을 저장하고 기존 Snapshot을 참조한다.
`SOURCE_VERSION_CONFLICT`는 Snapshot 없이 Operation과 시도 version, 비교 canonical
contract, 안전한 failure code를 append-only로 저장한다.

Run/Attempt가 비교한 canonical contract에는 `canonical_checksum`, `schema_version`,
`parser_version`, `normalization_version`, `canonicalization_spec_version`,
`endpoint_receipt_hash`, `rejected_record_count`가 포함된다. 후속 migration은 이 값을
`rag_source_ingestion_run`의 시도 provenance로 저장하며 Snapshot 컬럼으로 대체하지 않는다.

문법 검증을 통과한 값은 시도한 `source_version`과 nullable `external_version`을 그대로
보존한다. 문법 자체가 invalid인 값은 원문을 DB·로그·failure message에 저장하지 않고,
UTF-8 byte의 lowercase SHA-256, byte length, 안전한 validation reason code만 저장한다.
따라서 제어문자나 비정상적으로 긴 입력도 감사에서 exact raw value로 재노출하지 않는다.

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

`SourceSnapshotPolicy`의 기본값은 `max_rejected_records=0`,
`max_rejection_rate=0`, `empty_result_policy=REJECT`인 fail-closed 정책이다.
따라서 정책을 명시하지 않은 Source는 거부 레코드가 1건이라도 있으면
`FAILED/REJECTION_LIMIT_EXCEEDED`가 되며, 위 표의 “거부가 두 Hard Limit 이하”
경로에는 도달하지 않는다. `SUCCEEDED_WITH_REJECTIONS`와 `PENDING` 후보는
Source별 양수 Hard Limit을 명시적으로 설정한 경우에만 허용한다. 이는 #362 본문의
“기본값은 기존 동작과 동일” 요구를 대체하며 기존의 묵시적 거부 허용 동작을 유지하지 않는다.

## Freshness와 저장 상태

Snapshot의 `PENDING`, `CURRENT`, `STALE`, `FAILED` 상태 전이와 DB 소유권 규칙은
[Source Snapshot DB 상태 전이 Decision](./2026-09-08-source-snapshot-db-transition.md)을
따른다. 이 Decision은 해당 전이를 변경하지 않고, 승인된 Snapshot의 Freshness 정본
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
| `SNAPSHOT_NOT_APPROVED` | `PENDING` 상태이거나 거부 레코드의 publication 승인 누락 |
| `SNAPSHOT_VALIDATION_FAILED` | 저장된 Snapshot 상태가 `FAILED` |
| `SNAPSHOT_SUPERSEDED` | 승인 이력은 있으나 더 최신 Snapshot으로 대체되어 상태가 `STALE` |
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

Snapshot Receipt는 실제 생성된 Snapshot의 version·hash·승인 참조를 제공한다.
Ingestion Attempt Receipt는 nullable `source_snapshot_id`, 시도 version 또는 invalid-value
hash·length, 비교 canonical contract, `CREATED|NO_CHANGE|SOURCE_VERSION_CONFLICT|VALIDATION_FAILED`
판정을 제공한다. Snapshot Receipt만으로 `NO_CHANGE`, conflict, invalid 시도의 provenance를
대체하지 않는다.

## PD-362 단계 경계

이 Decision의 구현은 다음 세 단계로 나눈다.

1. **#377 완료** — Production `source_version` 생성·검증과 Candidate 계약 검토.
2. **#393 구현 완료** — failure Run 저장 연결, 거부 건수·비율 Hard Limit,
   `empty_result_policy`, Snapshot 승인·사용 가능 판정과 fail-closed reason code 산출.
3. **후속** — fail-closed reason code의 Catalog·Runtime 실제 소비자 연결, Source별 정책값과
   `external_version`·attempt provenance DB 저장, DB 왕복 Receipt와 PostgreSQL 통합 검증,
   #178 Freshness 계산.

#393까지는 판정을 산출해 실패 Run과 보존 Artifact에 연결하는 데까지이며, Catalog·Runtime
소비자는 아직 이 판정을 호출하지 않는다.

#362는 3단계가 끝날 때까지 열린 상태로 유지한다. #393 병합만으로 #362 완료나
Production Source·Catalog·Runtime 활성화를 선언하지 않는다.

## 구현 순서

1. Production `source_version` 생성·검증
2. 실패 코드와 실패 Run 저장 연결
3. Source별 거부·빈 결과 정책 적용
4. Snapshot 승인·사용 가능 판정 구현
5. #369 병합 후 최신 Alembic head에서 DB 컬럼·제약 연결
6. Catalog·Runtime 소비자 계약 테스트와 Receipt 검증


## #429 병합 이후 구현 상태 — #362 후속

`362a1b2c3d4e`와 `362b2c3d4e5f` migration 및 Python 저장소에서 Source 정책,
Snapshot external version, Snapshot/Citation 200자 상한과 Run 시도 provenance를 구현했다.
위 문서의 “DB 미저장/255자” 설명은 #377/#393 시점의 이력이며 이 후속 구현에서는 해소한다.
DB Trigger·RLS·업무 DB 함수는 도입하지 않는다. #398에서 Python으로 이관한 상태 전이를 유지한다.

실제 Snapshot Receipt와 Attempt Receipt를 별도로 조회하며 Source Writer 선택 전에
version/hash/Receipt 결속을 검증한다. Catalog/Runtime 실제 소비와 #178 Freshness 계산은
해당 담당 작업에 연결한다. #178 계산 구현은 #362 이슈의 명시적 제외 범위다.
전체 종료를 선언하지 않고 [#362/#165 공동 완료 조건](../../testing/source-policy-persistence-362.md)을 따른다.
