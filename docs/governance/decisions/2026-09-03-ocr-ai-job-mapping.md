# Product Decision: OCR Job과 공통 AI Job 연결 기준

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-212-20260903` |
| 상태 | Proposed |
| 결정 요청일 | 2026-09-03 |
| 결정 요청자 | 김지혜 (`@Jye-rookie`) |
| 결정자 | 권가빈 (`@hazelnutflavoured`) |
| Backend·DB 검토 | 송은영 (`@phina-io`) |
| Worker·OCR 검토 | 김지혜 (`@Jye-rookie`) |
| 추적 Issue | [#212](https://github.com/AI-HealthCare-05/AH_05_04/issues/212) |
| 관련 선행 작업 | #146 / PR #205 |
| 후속 작업 | #148 |
| 적용 범위 | `ocr_job.ai_job_id` 모델·Migration·DB 제약·검증 |

## 1. 배경

Post-MVP-1 비동기 실행 구조에서는 OCR 요청을 공통 `AI_JOB`으로 접수하고, 실제 OCR 결과를 저장하는 `ocr_job`과 해당 실행 상태를 관리하는 `ai_job`을 연결해야 한다.

현재 `ai_job`, `ai_job_attempt`, `outbox_event`, `idempotency_record`, `message_quarantine`, `dlq_outbox_event` 테이블은 #146 및 PR #205를 통해 추가되었다. 그러나 `ocr_job.ai_job_id`는 PR 범위를 분리하기 위해 포함되지 않았다.

따라서 현재 상태에서는 다음 두 정보를 DB 관계로 연결할 수 없다.

- 공통 비동기 실행 상태를 관리하는 `ai_job`
- OCR 처리 결과와 검수 상태를 관리하는 `ocr_job`

기존 OCR 행은 공통 AI Job 도입 이전에 생성됐으며, 당시 실행 event·attempt·Outbox를 검증할 수 있는 근거가 없다. 기존 행을 위해 synthetic `ai_job`을 생성하면 실제로 존재하지 않았던 비동기 실행 이력을 사후에 만들어내게 된다.

이 Decision은 기존 데이터를 왜곡하지 않으면서 신규 비동기 OCR 요청부터 두 모델을 연결하기 위한 DB 기준을 확정한다.

## 2. 결정 목표

다음 사항을 명확히 결정한다.

1. `ocr_job.ai_job_id`의 nullable 여부
2. `ai_job.id`에 대한 FK와 삭제 동작
3. OCR Job과 AI Job 사이의 cardinality
4. 기존 OCR 행의 처리 방식
5. 신규 비동기 OCR 접수 시 연결 책임
6. OCR 결과 소유권 검증 경로
7. downgrade 시 데이터 유실 방지 기준
8. #212와 #148의 구현 경계

## 3. 결정

### 3.1 `ocr_job.ai_job_id` 추가

`ocr_job` 테이블에 다음 컬럼을 추가한다.

```text
ai_job_id CHAR(36) NULL
```

애플리케이션 모델에서는 저장소의 기존 UUID 표현과 동일하게 `UUIDChar()`를 사용한다.

해당 컬럼은 nullable이다.

nullable로 결정한 이유는 다음과 같다.

- 기존 OCR 행에는 대응하는 공통 AI Job이 존재하지 않는다.
- 기존 실행 사실을 재구성할 신뢰 가능한 event·attempt·Outbox 근거가 없다.
- 기존 OCR 결과를 유지하면서 신규 비동기 OCR 흐름부터 점진적으로 연결해야 한다.
- AI Job 보존기간 만료 후에도 OCR 결과는 보존될 수 있어야 한다.

### 3.2 Foreign Key

`ocr_job.ai_job_id`는 `ai_job.id`를 참조한다.

```text
FOREIGN KEY (ai_job_id)
REFERENCES ai_job(id)
ON DELETE SET NULL
```

존재하지 않는 `ai_job.id`는 저장할 수 없다.

AI Job이 보존기간 만료 또는 승인된 정리 절차로 삭제되더라도 OCR 결과 행은 삭제하지 않는다. 이 경우 `ocr_job.ai_job_id`만 `NULL`로 전환한다.

`ON DELETE CASCADE`는 사용하지 않는다.

### 3.3 일대일 연결

하나의 `ai_job`은 최대 하나의 `ocr_job`에만 연결할 수 있다.

이를 DB에서 강제하기 위해 `ocr_job.ai_job_id`에 unique 제약을 추가한다.

제약 이름은 다음과 같이 고정한다.

```text
uq_ocr_job_ai_job
```

PostgreSQL은 nullable unique 컬럼의 복수 `NULL`을 허용하므로 기존 OCR 행과 삭제된 AI Job의 연결 해제 상태를 유지할 수 있다.

하나의 OCR Job이 여러 AI Job 실행 이력을 직접 참조하는 구조는 이번 범위에 포함하지 않는다. 실행별 이력은 `ai_job_attempt`가 관리한다.

### 3.3.1 AI Job 유형 및 전체 도메인 연결 불변조건

`ocr_job.ai_job_id`는 `ai_job.job_type = 'OCR'`인 AI Job만 참조할 수 있다.

또한 하나의 AI Job은 OCR·Guide·Chat을 포함한 전체 도메인에서 최대 하나의
결과 row에만 연결할 수 있다. 예를 들어 하나의 AI Job이 `ocr_job`과 향후
`guide` 또는 `chat_message` 결과 row에 동시에 연결되어서는 안 된다.

`ocr_job.ai_job_id`의 FK와 unique 제약은 다음 항목만 DB에서 직접 보장한다.

- 참조하는 AI Job의 존재
- 하나의 AI Job이 두 개 이상의 `ocr_job`에 연결되지 않음

일반 FK와 개별 도메인 테이블의 unique 제약만으로는 다음 항목을 보장할 수 없다.

- OCR Job이 `job_type='OCR'`인 AI Job만 참조하는지
- 하나의 AI Job이 서로 다른 도메인 결과 row에 동시에 연결되지 않는지

따라서 AI Job 유형과 전체 도메인 연결 불변조건의 검증 책임은 공통 Job 접수
Service가 가진다.

Service는 도메인 결과를 연결하기 전에 다음 순서로 처리한다.

1. 대상 `ai_job` row를 `SELECT ... FOR UPDATE`로 잠근다.
2. 요청 도메인과 `ai_job.job_type`이 일치하는지 확인한다.
3. 지원되는 모든 도메인 결과 테이블에서 해당 `ai_job_id`의 기존 연결이 없는지 확인한다.
4. 도메인 결과 row와 `ai_job_id` 연결을 같은 transaction에서 생성한다.
5. 유형 불일치나 기존 도메인 연결이 있으면 전체 transaction을 rollback한다.

향후 Guide·Chat 연결도 도메인별 독립 검증을 구현하지 않고 동일한 공통 검증
경로를 사용해야 한다. 모든 도메인 연결이 같은 `ai_job` row를 먼저 잠그므로
서로 다른 도메인에서 동시에 연결을 시도해도 하나만 성공할 수 있다.

Service를 우회하여 `ocr_job.ai_job_id`를 직접 쓰는 것은 허용하지 않는다.

### 3.4 기존 OCR 행 처리

Migration 적용 시 기존 `ocr_job` 행의 `ai_job_id`는 모두 `NULL`로 유지한다.

다음 작업은 금지한다.

- 기존 OCR 행별 synthetic `ai_job` 생성
- 기존 OCR 행별 synthetic `outbox_event` 생성
- OCR 생성 시각만으로 과거 AI Job을 추정하여 연결
- 기존 OCR 상태를 기반으로 임의의 `COMPLETED` AI Job 생성
- 검증되지 않은 과거 로그 또는 사용자 데이터를 사용한 backfill
- 기존 OCR 행을 삭제하거나 재생성

이번 Migration은 컬럼과 제약만 추가하며 기존 OCR 실행 이력을 재구성하지 않는다.

### 3.5 신규 OCR 연결 시점

신규 비동기 OCR 접수에서는 하나의 DB transaction 안에서 다음 순서로 처리한다.

1. 공통 `ai_job`을 `job_type='OCR'`로 생성한다.
2. 해당 `ai_job` row를 잠그고 공통 도메인 연결 검증을 수행한다.
3. 기존 OCR·Guide·Chat 결과 연결이 없음을 확인한다.
4. 대응하는 `outbox_event`를 생성한다.
5. 신규 `ocr_job`을 생성한다.
6. `ocr_job.ai_job_id = ai_job.id`로 연결한다.
7. 필요한 멱등성 레코드를 생성한다.

`job_type`이 `OCR`이 아니거나 다른 도메인 결과가 이미 연결된 경우에는
`ocr_job`을 생성하지 않고 전체 transaction을 rollback한다.

이 transaction 중 하나라도 실패하면 전체 접수를 rollback해야 한다.

다만 실제 접수 Service와 API 연결은 #212가 아니라 #148의 구현 범위다.

#212에서는 다음 항목만 구현한다.

- ORM 모델 컬럼
- Migration
- FK
- unique 제약
- 기존 행 `NULL` 유지
- downgrade 안전 가드
- 모델·Migration·DB 동작 테스트
- 데이터 스키마 문서 갱신

### 3.6 소유권 검증

`ocr_job.ai_job_id`는 실행 연결 식별자이며 사용자 리소스 소유권의 단독 기준이 아니다.

OCR 결과 소유권은 기존 경로를 유지한다.

```text
ocr_job
→ medical_document
→ profile_id
→ 인증 사용자의 SELF profile
```

Job 조회 시 `ai_job.user_id`로 1차 범위를 제한할 수 있지만, OCR 결과를 사용자에게 공개하기 전에는 `ocr_job → medical_document → profile_id` 경로를 다시 확인한다.

다음 동작은 금지한다.

- `ocr_job.ai_job_id`가 존재한다는 이유만으로 결과 공개
- `ai_job.user_id`만 확인하고 OCR 결과 소유권 검증 생략
- 다른 사용자의 OCR Job 존재 여부를 오류 응답으로 노출
- `ocr_job.profile_id` 중복 컬럼 추가

교차 사용자 접근은 기존 계약에 따라 `404`로 처리한다.

### 3.7 삭제 및 보존

AI Job 실행 메타데이터와 OCR 결과는 보존 목적과 기간이 다를 수 있다.

따라서 다음 기준을 적용한다.

- `ai_job` 삭제는 `ocr_job` 삭제로 전파하지 않는다.
- `ai_job` 삭제 시 `ocr_job.ai_job_id`는 `NULL`이 된다.
- `ocr_job` 삭제가 필요한 경우 기존 OCR·의료문서 삭제 정책을 따른다.
- 단순히 `ai_job_id`가 `NULL`이라는 이유로 OCR 결과를 삭제하지 않는다.
- legal hold 또는 별도 보존 승인이 있으면 해당 정책을 우선한다.

### 3.8 Downgrade 안전 가드

Migration downgrade는 `ocr_job.ai_job_id` 컬럼을 제거하기 전에 non-null 연결 존재 여부를 검사한다.

다음 조건이면 downgrade를 중단한다.

```text
SELECT COUNT(*)
FROM ocr_job
WHERE ai_job_id IS NOT NULL;
```
조회 결과가 0보다 크면 downgrade를 중단한다.

`COUNT(ai_job_id)`도 사용할 수 있지만, 구현에서는 조건의 의미가 명확한
`COUNT(*) ... WHERE ai_job_id IS NOT NULL` 형태를 사용한다.

`COUNT(ai_job_id IS NOT NULL)`은 사용하지 않는다. PostgreSQL에서
`ai_job_id IS NOT NULL`은 `TRUE` 또는 `FALSE`를 반환하고 두 값 모두
non-null이므로, 연결되지 않은 기존 OCR 행까지 모두 계산하기 때문이다.

연결 데이터가 존재하는 상태에서 컬럼을 제거하면 OCR Job과 AI Job 사이의 검증 가능한 연결 정보가 유실되기 때문이다.

downgrade가 반드시 필요한 경우 다음 절차가 선행되어야 한다.

1. 운영 승인 획득
2. 연결 데이터 보존 또는 명시적 폐기 기준 기록
3. 영향받는 OCR Job 수 확인
4. rollback 또는 forward-fix 계획 기록
5. 승인된 방식으로 `ai_job_id` 연결 해제
6. non-null 행이 0건임을 다시 검증
7. downgrade 실행

Migration 코드가 임의로 `ai_job_id`를 `NULL`로 변경한 뒤 컬럼을 제거해서는 안 된다.

## 4. 구현 대상

### 4.1 ORM 모델

대상 파일:

```text
backend/app/models/ocr.py
```

`OcrJob`에 다음 필드를 추가한다.

```python
ai_job_id: Mapped[UUID | None] = mapped_column(
    UUIDChar(),
    ForeignKey("ai_job.id", ondelete="SET NULL"),
    nullable=True,
)
```

테이블 제약에는 다음 unique 제약을 추가한다.

```python
UniqueConstraint(
    "ai_job_id",
    name="uq_ocr_job_ai_job",
)
```

필요한 경우 `AiJob` relationship을 추가할 수 있지만, relationship 추가가 순환 import나 불필요한 cascade를 만들면 FK 컬럼 구현만 유지한다.

### 4.2 Migration

새 Alembic revision에서 다음 순서로 적용한다.

1. `ocr_job.ai_job_id` nullable 컬럼 추가
2. `ai_job.id`를 참조하는 FK 추가
3. `ON DELETE SET NULL` 지정
4. `uq_ocr_job_ai_job` unique 제약 추가
5. 기존 행이 모두 `NULL`인지 검증
6. Migration 적용 결과 기록

기존 PR #205 Migration 파일을 직접 수정하지 않는다. 병합된 Migration을 변경하지 않고 새 revision을 추가한다.

### 4.3 문서

다음 문서를 실제 구현과 함께 갱신한다.

```text
docs/data-schema.md
docs/contracts/proposed/track-a-migration-rollback-v1.md
```

문서에는 다음 내용을 반영한다.

- `ocr_job.ai_job_id` nullable FK
- `ON DELETE SET NULL`
- unique 연결
- 기존 행 `NULL`
- synthetic Job 및 backfill 금지
- 실제 신규 OCR 접수 연결은 #148 범위

## 5. 구현하지 않는 범위

이번 Decision과 #212에서는 다음을 구현하지 않는다.

- 공통 Job 조회 API
- OCR 접수 API의 실제 비동기 전환
- Guide·Chat `202 Accepted` 전환
- OCR Worker Handler
- Redis Consumer
- Worker reclaim·retry·DLQ
- 기존 OCR 행 backfill
- Guide의 `ai_job_id` 연결
- Chat Message의 `ai_job_id` 연결
- `ocr_job.profile_id` 추가
- AI Job 보존·삭제 배치
- 사용자에게 공개되는 신규 API 또는 DTO
- 새로운 Job 상태나 오류 코드

위 항목은 #148 또는 별도 승인된 후속 Issue에서 처리한다.

## 6. 불변조건

구현과 테스트는 다음 불변조건을 보장해야 한다.

1. 기존 OCR 행은 Migration 후에도 유지된다.
2. 기존 OCR 행의 `ai_job_id`는 `NULL`이다.
3. 존재하지 않는 AI Job은 참조할 수 없다.
4. 하나의 AI Job은 두 개 이상의 OCR Job에 연결될 수 없다.
5. AI Job 삭제 시 OCR Job은 삭제되지 않는다.
6. AI Job 삭제 시 연결된 `ocr_job.ai_job_id`만 `NULL`이 된다.
7. OCR 결과 소유권은 기존 `medical_document.profile_id` 경로를 유지한다.
8. 연결 데이터가 존재하면 downgrade가 자동으로 진행되지 않는다.
9. Migration은 실제 환자·처방·OCR 데이터를 생성하지 않는다.
10. 로그와 테스트 출력에 의료정보 또는 비밀정보를 포함하지 않는다.
11. `ocr_job.ai_job_id`는 `job_type='OCR'`인 AI Job만 참조한다.
12. 하나의 AI Job은 OCR·Guide·Chat 전체에서 최대 하나의 도메인 결과 row에만 연결된다.

## 7. 필수 테스트

### 7.1 모델·스키마

- `ocr_job.ai_job_id` 컬럼 존재
- 컬럼 nullable 확인
- `ai_job.id` FK 확인
- `ON DELETE SET NULL` 확인
- `uq_ocr_job_ai_job` unique 제약 확인

### 7.2 DB 동작

- 유효한 AI Job을 OCR Job에 연결할 수 있음
- 존재하지 않는 AI Job 연결 시 FK 위반
- 동일 AI Job을 두 OCR Job에 연결하면 unique 위반
- 여러 기존 OCR Job이 `ai_job_id=NULL`인 상태로 공존
- AI Job 삭제 후 OCR Job 유지
- AI Job 삭제 후 `ocr_job.ai_job_id=NULL`

### 7.3 Service 연결 불변조건

다음 테스트는 실제 Job 접수 연결을 구현하는 #148에서 필수로 추가한다.

- `job_type='GUIDE'`인 AI Job을 OCR Job에 연결하면 거부되고 OCR row가 생성되지 않음
- `job_type='CHAT'`인 AI Job을 OCR Job에 연결하면 거부되고 OCR row가 생성되지 않음
- 이미 OCR 결과에 연결된 AI Job을 Guide·Chat 결과에 연결하면 거부됨
- 이미 Guide·Chat 결과에 연결된 AI Job을 OCR 결과에 연결하면 거부됨
- 서로 다른 도메인에서 같은 AI Job을 동시에 연결해도 하나만 성공
- 유형 또는 기존 연결 검증 실패 시 AI Job·Outbox·도메인 row·멱등성 변경 전체 rollback

### 7.4 Migration

- 기존 데이터가 있는 상태에서 upgrade 성공
- 기존 OCR 행의 `ai_job_id=NULL`
- 빈 DB에서 upgrade 성공
- `upgrade head` 중복 실행 시 추가 변경 없음
- non-null 연결이 없는 상태에서 downgrade 성공
- non-null 연결이 있는 상태에서 downgrade 차단
- downgrade 후 재-upgrade 성공

### 7.5 보안·개인정보

- 합성 UUID와 비식별 fixture만 사용
- 교차 사용자 결과 접근 404 경계 유지
- Migration·오류·테스트 로그에 OCR 원문 없음
- 실제 환자·처방·진료 데이터 없음

## 8. 검증 명령

```bash
uv run pytest backend/app/tests/models -q
uv run pytest tests/migration -q
uv run ruff check backend/app backend/alembic tests/migration
uv run ruff format backend/app backend/alembic tests/migration --check
uv run mypy backend/app
bash scripts/ci/run_test.sh
```

PostgreSQL에서 다음 항목도 확인한다.

```text
ocr_job.ai_job_id 컬럼과 nullable 여부
fk_ocr_job_ai_job 또는 승인된 FK 이름
ON DELETE SET NULL
uq_ocr_job_ai_job
기존 OCR 행의 non-null ai_job_id 건수
```

## 9. 대안 검토

### 대안 A: 기존 OCR 행마다 synthetic AI Job 생성

채택하지 않는다.

실제로 존재하지 않았던 Outbox·attempt·Worker 실행 이력을 만들어 감사 정확성을 훼손한다.

### 대안 B: `ocr_job.ai_job_id`를 NOT NULL로 추가

채택하지 않는다.

기존 OCR 행을 유지할 수 없으며 synthetic backfill을 강제하게 된다. 또한 AI Job 삭제 시 OCR 결과 보존도 어렵다.

### 대안 C: AI Job 삭제 시 OCR Job도 함께 삭제

채택하지 않는다.

실행 메타데이터의 보존 종료가 사용자 OCR 결과 삭제로 이어져서는 안 된다.

### 대안 D: FK 없이 문자열 식별자만 저장

채택하지 않는다.

존재하지 않는 AI Job 참조와 잘못된 연결을 DB에서 차단할 수 없다.

### 대안 E: `ai_job`에 `ocr_job_id` 저장

채택하지 않는다.

도메인별 nullable FK를 공통 Job 테이블에 계속 추가해야 하며, 도메인 결과와 공통 실행 상태의 책임 경계가 흐려진다. 승인된 목표 계약은 도메인 결과 행이 `ai_job_id`를 보유하는 방향이다.

## 10. 영향

### 긍정적 영향

- 공통 AI Job과 OCR 결과의 관계를 DB에서 검증할 수 있다.
- 기존 OCR 데이터를 왜곡하지 않고 점진적으로 비동기 구조를 도입할 수 있다.
- AI Job 삭제와 OCR 결과 보존을 분리할 수 있다.
- #148에서 신규 OCR 접수 transaction을 구현할 명확한 연결 지점이 생긴다.
- 공통 Job 조회 시 OCR 결과 reference를 안전하게 구성할 수 있다.

### 비용과 주의점

- 기존 OCR 행은 공통 Job 조회로 역추적할 수 없다.
- `ai_job_id=NULL`이 기존 동기 OCR인지 삭제된 AI Job의 결과인지 컬럼 하나만으로 구분되지 않는다.
- 필요하다면 향후 별도 감사 메타데이터를 승인된 Issue에서 추가해야 한다.
- downgrade는 연결 데이터가 존재하면 수동 승인 절차가 필요하다.

## 11. 승인 조건

다음 검토가 완료되기 전에는 상태를 `Approved`로 변경하지 않는다.

- [ ] 권가빈: 기존 OCR 행 synthetic Job·backfill 금지 승인
- [ ] 권가빈: nullable FK와 `ON DELETE SET NULL` 승인
- [ ] 송은영: Backend·DB 모델 및 Migration 경계 승인
- [ ] 송은영: unique 제약과 downgrade 안전 가드 승인
- [ ] 김지혜: Worker·OCR 연결 경계 확인
- [ ] #212 Issue에 승인 근거 댓글 또는 리뷰 링크 기록
- [ ] 승인 결과와 문서 내용이 일치하는지 재확인

승인 후 문서 상단의 상태를 다음과 같이 변경한다.

```text
Proposed → Approved
```

승인 없이 Model·Migration·공유 계약 구현을 병합하지 않는다.

## 12. 후속 연결

### #212

이 Decision 승인 후 다음을 구현한다.

- `OcrJob.ai_job_id`
- 신규 Alembic Migration
- FK·unique 제약
- downgrade 안전 가드
- 모델·Migration·DB 테스트
- 데이터 스키마 문서 갱신

### #148

#212 병합 후 다음을 구현한다.

- 신규 비동기 OCR 접수 transaction에서 `ocr_job.ai_job_id` 연결
- 공통 Job 조회 API
- OCR·Guide·Chat `202 Accepted` 전환
- Job 상태와 도메인 결과 조회 경계
- 소유권·404·Rediscovery 검증

### #142

#142의 reclaim·retry·DLQ 구현은 이 Decision의 DB 스키마를 변경하지 않는다. 다만 OCR Worker가 최종 결과를 저장할 때 `ocr_job.ai_job_id` 관계와 소유권 경계를 유지해야 한다.

## 13. 변경 이력

| 날짜 | 변경 | 작성자 |
| --- | --- | --- |
| 2026-09-03 | PR #239 리뷰 반영: downgrade COUNT 조건 수정, AI Job 유형·전체 도메인 연결 불변조건 및 Service 검증 책임 명시 | 김지혜 |
| 2026-09-03 | 최초 Proposed Decision 작성 | 김지혜 |
