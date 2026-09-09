# Issue #362 Source Snapshot 정책 검증

## 범위

이 단계는 #377에서 확정한 Source version 계약을 바탕으로 다음 DB 비의존 정책을 구현한다.

- Source version 검증 실패를 안전한 고정 코드로 분류
- 문법 오류 원문을 저장하지 않고 SHA-256·UTF-8 byte 길이·reason code로 변환
- Source별 거부 건수·비율 Hard Limit 판정
- `empty_result_policy=REJECT` 적용
- 정책 거부 시 Snapshot 없이 `FAILED` Run 생성
- Catalog·Runtime의 Snapshot 사용 가능 여부를 fail-closed로 판정

## Source version 실패 코드

| 코드 | 조건 |
| --- | --- |
| `SOURCE_VERSION_INVALID` | 문법·NFC·길이·공백·제어문자·checksum 형식 오류 |
| `SOURCE_VERSION_BINDING_MISMATCH` | external version 또는 API·Internal checksum 결속 불일치 |
| `SOURCE_VERSION_CONFLICT` | 이미 관측한 동일 version의 canonical contract 불일치 |

문법 자체가 잘못된 `source_version` 원문은 감사 정보에 포함하지 않는다. 원문의 UTF-8 byte에 대한 lowercase SHA-256, byte 길이와 `SOURCE_VERSION_INVALID`만 사용한다.

## Snapshot 후보 정책

거부율은 다음 식으로 계산한다.

```text
rejection_rate = rejected_record_count / record_count
```

두 Hard Limit은 자동 승인 허용치가 아니다.

| 입력 | 결과 |
| --- | --- |
| `record_count=0`, `empty_result_policy=REJECT` | `FAILED/EMPTY_RESULT`, Snapshot 없음 |
| 거부 건수 또는 비율이 Hard Limit 초과 | `FAILED/REJECTION_LIMIT_EXCEEDED`, Snapshot 없음 |
| 거부 0건 | `PENDING` 후보 |
| 거부가 두 Hard Limit 이하 | `PENDING` 후보, 별도 publication 승인 필요 |

임계값과 같은 값은 허용하며, 하나라도 초과하면 실패한다.

## Catalog·Runtime 사용 가능 판정

다음 조건을 모두 만족한 Snapshot만 사용할 수 있다.
- provenance 검증 통과
- verification_status=CURRENT
- 거부 레코드가 있으면 publication 승인 통과
- 승인된 Freshness Policy 기준 사용 가능

차단 reason code는 다음과 같다.

| 코드 | 조건 |
| --- | --- |
| `SNAPSHOT_PROVENANCE_INVALID` | Version·Hash·Receipt 결속 누락 또는 불일치 |
| `SNAPSHOT_REJECTED` | Snapshot 검토 결과 거부 |
| `SNAPSHOT_NOT_APPROVED` | 승인 대기 또는 publication 승인 누락 |
| `SNAPSHOT_FRESHNESS_STALE` | Freshness Policy 기준 사용 불가 |

판정 우선순위는 provenance 불일치, Snapshot 거부, 승인 누락, Freshness 부적합 순서다.

## 이번 단계에서 제외하는 범위

다음 항목은 #369 병합 후 최신 Alembic head에서 별도 PR로 구현한다.
- Source별 정책값 DB 저장
- rag_source_snapshot.external_version 저장
- source_version 200자 DB 제약
- Ingestion Run/Attempt의 attempted version과 canonical contract 저장
- invalid version hash·byte 길이·reason code DB 저장
- NO_CHANGE와 SOURCE_VERSION_CONFLICT의 append-only 시도 provenance
- Version·Hash·Receipt PostgreSQL 왕복 검증
- Catalog·Runtime 실제 소비자 연결
- #178 Freshness 계산 구현

## 검증 명령
```bash
uv run pytest ai_worker/tests/rag/source_ingestion -q
uv run pytest tests/contract/test_rag_source_governance_receipt.py -q
uv run ruff check ai_worker/tasks/rag/source_ingestion ai_worker/tests/rag/source_ingestion
uv run ruff format ai_worker/tasks/rag/source_ingestion ai_worker/tests/rag/source_ingestion --check
uv run mypy ai_worker/tasks/rag/source_ingestion
git diff --check
```
