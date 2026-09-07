# #165 Source 수집 구현 진행 기록

## 현재 검증 상태

- Source ingestion 단위 테스트: 262 passed
- AI Worker 전체 테스트: 1032 passed
- PostgreSQL Snapshot lifecycle·acquisition lock·실패 이력 통합 테스트: 10 passed
- Source/Catalog·Artifact Migration 테스트: 12 passed
- 전체 Migration 테스트: 51 passed
- Backend·계약·통합 전체 테스트: 1131 passed, 2 skipped
- Ruff 전체 검사 통과, 495 files already formatted
- Mypy: 446개 소스 파일 통과
- 실제 Worker 이미지에서 Source Snapshot adapter import·PostgreSQL 쿼리 통과
- S3 호환 비공개 Object Storage adapter·SDK 계약 테스트 통과
- 실제 MFDS 호출은 이번 검증에 포함하지 않는다.
- Runtime Bundle 활성화는 아직 연결하지 않았다.

## 구현한 범위

- 원본 바이트의 SHA-256 계산
- 수집 결과 완전성과 Endpoint Receipt 상태·식별자 검사
- 파서 입력 레코드 수집 및 원본과 분리된 복사
- checksum용 JSON 직렬화
- 제품 레코드의 ITEM_SEQ 정렬과 canonical checksum 계산
- 전체 페이지 제품 레코드와 checksum 계산 연결
- Raw Artifact 메타데이터 형식 검사와 manifest checksum 계산
- 로컬 원본 파일의 실제 크기·SHA-256 검사
- 전달된 Artifact 파일 전체 검증 후 manifest checksum 반환
- 대표 ASCII 입력·canonical bytes·기대 SHA-256 고정 테스트
- Endpoint Receipt 자체 hash와 연결된 합성 fixture 무결성 검증
- 검증된 제품 Receipt를 Parser와 canonical checksum 계산 경계에 연결
- 수집 페이지와 Raw Artifact의 누락·중복·checksum·content type 불일치 검증
- 검증한 동일 원본 바이트의 MFDS 응답을 파싱하고 수집 레코드·전체 건수와 결속
- 원본에 없거나 원본 이후 변경된 메모리 레코드의 checksum 입력 차단
- 제품 canonicalization 규칙을 `mfds-product-approval@1`로 고정
- Endpoint Receipt와 현재 MFDS 제품 Operation 계약의 HTTP·응답 코드·pagination·제한 설정 일치 검증
- Receipt 필드 누락과 bool·int 타입 혼동을 중첩 계약까지 exact-match로 차단
- 제품 성공·빈 결과·인증 실패·일일 한도·schema drift 필수 fixture 시나리오 검증
- Receipt hash·Raw Manifest checksum·canonical checksum·버전·record count를 묶은 `ProductIngestionResult` 계약 추가
- 검증 결과에 Source version·schema·parser·normalization version과 실행 metadata를 결합하는 저장 계약 추가
- Source·Endpoint·Operation exact-match 조회와 Source 행 잠금 경계 추가
- 신규 결과를 승인 전 `PENDING` Snapshot과 append-only 검증·수집 이력으로 저장
- 동일 canonical 내용과 동일 version 계약의 재수집을 기존 Snapshot의 `NO_CHANGE` 이력으로 저장
- 동일 외부 Source version의 canonical 내용 또는 version 계약 변경을 `SOURCE_VERSION_CONFLICT`로 차단
- 직전 Snapshot 기준 비교로 `A → B → A`를 세 개의 append-only Snapshot으로 보존
- commit·rollback은 Worker 실행 transaction이 소유하고 저장 어댑터가 직접 실행하지 않도록 분리
- 검증된 `PENDING` Snapshot의 `CURRENT` 선택과 기존 `CURRENT → STALE` 전이
- 이전 `STALE` Snapshot을 다시 `CURRENT`로 선택하는 검증 상태 복원
- `FAILED` Snapshot 선택 차단과 고정 failure code만 남기는 실패 이력
- 실제 PostgreSQL에서 transaction rollback과 Operation 잠금 동시성 검증
- 검증된 Raw Artifact를 수집 실행별 접근 통제 저장소 참조로 연결
- Artifact 개수·페이지 중복·Raw Manifest checksum을 DB 접근 전에 재검증
- `CREATED`, `NO_CHANGE`, `SOURCE_VERSION_CONFLICT` 실행 모두의 원본 참조 보존
- 원본 참조 append-only 제약과 데이터 존재 시 downgrade 차단
- 로컬 비공개 저장소의 SHA-256 내용 주소, 디렉터리 `0700`, 파일 `0600` 적용
- 복사 중 크기·SHA-256 재검증과 임시 파일 완료 후 원자적 공개
- 동일 원본 재시도의 불변 객체 재사용과 기존 객체 변조 차단
- manifest 불일치를 파일·DB 쓰기 전에 차단하는 단일 보관·저장 진입점
- `RAW_RESPONSE`와 `REJECTS` Artifact 종류 및 메타데이터 조합을 DB CHECK로 제한
- 거부 건수가 있으면 REJECTS 원본 참조를 요구하고, 거부 건수가 없으면 REJECTS 저장을 차단
- REJECTS에는 안전한 고정 `reject_code`와 원문 없는 `parser_location`만 기록
- REJECTS 데이터가 존재할 때 관련 필드를 제거하는 downgrade 차단
- DB 문자열 길이 제한과 `parser_location` 제어문자를 Artifact 보존 전에 차단
- RAW_RESPONSE와 REJECTS를 합친 수집 실행 전체에서 중복 Artifact key를 파일 보존 전에 차단
- 외부 Source 호출 전에 Operation 행을 `SKIP LOCKED`로 선점해 동시 acquisition을 즉시 차단
- 같은 Source의 서로 다른 Operation 동시 요청에서도 Provider 호출이 한 번만 실행되는 PostgreSQL 통합 테스트
- Provider 수집 실패를 안전한 `SourceFailureCode`로 Snapshot 없이 `FAILED` Run에 기록
- Parser 검증과 거부 한도 초과를 고정 실패 코드로 기록하고 보존된 원본 Artifact 참조 연결
- 실패 Source run의 부분 page, 거부 Artifact 누락과 실패 실행 내 Artifact 중복을 DB 접근 전에 차단
- Worker 이미지의 runtime DB 설정으로 엔진·session·Source Snapshot adapter를 조립하는 smoke 진입점 추가
- 실제 Worker 이미지에서 `rag_source_snapshot` 조회를 실행해 패키징·드라이버·연결·테이블 접근을 함께 검증
- S3 호환 저장소의 SHA-256 내용 주소·조건부 생성·업로드 checksum·명시적 AES256 또는 KMS 암호화 검증
- 동일 객체 재시도 시 HEAD metadata·크기·content type·checksum·암호화 상태를 확인한 뒤에만 재사용
- Source Artifact 저장소를 기본 `DISABLED`로 두고 승인된 local root 또는 S3 bucket이 있을 때만 조립
- S3 credential을 설정 모델·DB에 저장하지 않고 AWS SDK 표준 credential provider chain으로만 주입
- S3 endpoint의 평문 HTTP·URL credential·query·fragment를 설정 검증에서 차단
- Provider·transport 오류를 endpoint·credential 세부정보가 없는 고정 오류로 변환

## 최종 검수 결과

- Artifact와 Snapshot 저장 메타데이터의 길이를 DB column 제한과 대조했다.
- `parser_location`은 Unicode 제어문자를 허용하지 않아 DB CHECK보다 늦게 실패하지 않도록 했다.
- RAW_RESPONSE와 REJECTS 사이의 중복 Artifact key를 DB unique 제약 도달 전에 거부한다.
- 위 사전 검증 실패 시 내용 주소 객체와 DB row가 생성되지 않는 회귀 테스트를 추가했다.
- Source ingestion 단위 테스트, AI Worker 전체 테스트, PostgreSQL lifecycle·Migration 집중 테스트를 다시 실행했다.
- 현재 브랜치로 Worker 이미지를 빌드하고 로컬 PostgreSQL에 연결해 Source Snapshot 조회를 실행했다.

## Issue 완료 조건 대조

| 완료 조건 | 상태 | 현재 증빙 또는 남은 작업 |
| --- | --- | --- |
| 결정적 checksum·record count | 완료 | canonical vector와 전체 page 결속 테스트 |
| 부분 page·schema drift·version conflict 차단 | 완료 | 실패 Source run의 부분 page를 거부하고, 수집·Parser·거부 한도 실패를 Snapshot 없는 `FAILED` Run으로 기록 |
| `NO_CHANGE`, `A → B → A` lineage | 완료 | 단위·PostgreSQL 통합 테스트 |
| reject 원문 비로그·접근 통제 보존 | 완료 | DB에는 참조와 안전한 code·location만 저장 |
| Snapshot 불변성과 상태 전이 | 완료 | append-only 제약, `PENDING/CURRENT/STALE/FAILED` 전이 테스트 |
| rollback provenance | 부분 완료 | DB transaction rollback은 검증했다. 미참조 내용 주소 객체 정리 정책은 미확정 |
| 동일 Source 동시 acquisition 1회 | 완료 | 외부 호출 전 `SKIP LOCKED` 선점과 동시 Provider 1회 호출 PostgreSQL 테스트 |
| #166 Catalog build 인계 | 부분 완료 | 검증된 Snapshot 결과 계약은 제공한다. Catalog 적재·Runtime 연결은 #166 범위 |
| Worker DB adapter 실행 | 완료 | 실제 Worker 이미지에서 runtime 엔진·session·adapter 조립과 `rag_source_snapshot` 조회 통과 |
| 외부 Object Storage adapter | 완료 | S3 조건부 생성·checksum·암호화·재사용 검증과 표준 credential chain factory |
| Artifact 보존·정리 정책 | 결정 대기 | 승인 전 자동 삭제 없음. Runtime 저장소 기본 `DISABLED` |

## 확정된 제품 canonicalization 규칙

은영님과 현우님 검토 결과를 기준으로 제품 Source canonicalization을
`mfds-product-approval@1`로 고정한다.

- 저장되는 원문 값과 Unicode 형태를 변경하지 않는다.
- NFC와 trim을 적용하지 않는다.
- 숫자형 문자열을 숫자로 변환하지 않는다.
- 문자열·정수·boolean·null·빈 문자열·필드 누락을 구분한다.
- 정수는 `-(2^53)+1`부터 `2^53-1`까지만 허용한다.
- 실수와 lone surrogate를 거부한다.
- 중첩 객체 key는 UTF-16 byte 기준으로 정렬한다.
- 전체 제품 레코드는 `ITEM_SEQ` 원문 값으로 정렬한다.
- 레코드 내부 배열 순서는 유지한다.
- Operation Envelope 중 `header`, `totalCount`, `pageNo`, `numOfRows`는 제품 Parser가 반환한 레코드 목록에 포함하지 않는다.
- `body`의 미등록 Envelope 필드는 자동 제외하지 않고 schema drift로 거부한다.
- 원본 JSON 객체의 중복 key는 모든 깊이에서 거부하며 마지막 값으로 덮어쓰지 않는다.
- `items`, `pageNo`, `numOfRows`, `totalCount`의 누락과 pagination 필드의 비정수·boolean 값을 거부한다.
- 응답 `pageNo`는 요청 page와 일치해야 한다. `numOfRows`는 확인된 실응답 계약에 따라 존재와 정수 타입만 검증하며 요청값과의 일치를 추정하지 않는다.
- 1보다 작은 요청·응답 page, 음수 `totalCount`, `item` 이외의 wrapper 필드와 비표준 JSON 숫자 상수를 거부한다.

기존 Evaluation serializer는 변경하지 않았다. Source checksum과
Evaluation Manifest hash는 계산 범위와 제외 규칙이 다르므로 각각의
버전과 계약을 유지한다.

실제 MFDS 재수집에서 동일 값의 Unicode 형태가 달라지는 현상이
발생하는지는 후속 실측으로 확인한다. 문제가 확인되면 기존 버전의
결과를 덮어쓰지 않고 새 Source 전용 규칙과 버전을 추가한다.
검색용 파생값과 Resource 경로 정규화는 별도 범위로 다룬다.

## #164 연결 후 반영한 범위

- #291의 Source·Endpoint·Operation·Snapshot·Verification·Ingestion Run 테이블 연결
- 수집 실행 계층이 선택한 `source_version`, schema·parser·normalization version과 실행 metadata 전달
- 같은 Source의 lifecycle 판단 전 Source 행 잠금
- 신규 Snapshot 후보 생성과 `PASSED` 검증·성공 Run 기록
- `NO_CHANGE`, `A → B → A`, 동일 Source version 충돌 판단과 append-only 이력
- Snapshot 후보를 `PENDING`으로 유지해 승인과 Runtime 활성화가 자동으로 일어나지 않는 경계
- 검증 상태의 `PENDING → CURRENT`, 기존 `CURRENT → STALE`와 이전 Snapshot 복원
- 실제 PostgreSQL transaction rollback과 같은 Source의 동시 판단 직렬화

## 남은 범위

다음 항목은 #165를 완전히 닫기 위해 남아 있다.

- DB rollback 뒤 참조되지 않은 내용 주소 객체의 보존·정리 정책
- REJECTS 세부 보존 기간과 승인된 reject code 목록 확정

Catalog 적재와 Runtime Bundle 활성화 연결은 #166 범위로 유지한다.

여기서 `CURRENT`는 #291에 정의된 검증·최신성 상태다. 이전 Snapshot을
`CURRENT`로 복원해도 Runtime Release Bundle은 변경하지 않는다. 실제 Runtime
활성화와 rollback은 승인된 Bundle 경계에서 별도로 수행한다.

현재 `ProductIngestionResult`가 identity, Endpoint Receipt hash,
Raw Manifest checksum, canonical checksum, canonicalization spec version과
record count를 제공한다. 저장 호출자는 다음 값을 원본이나 checksum에서
추론하지 않고 승인된 수집 실행 provenance에 따라 함께 전달한다.

- `source_version`
- `schema_version`
- `parser_version`
- `normalization_version`
- `rejected_record_count`
- `collected_at`
- `run_group_key`, `attempt_number`, 실행 시작·종료 시각

DB commit·rollback은 기존 Worker transaction 경계를 재사용한다. 동일
Source의 판단과 저장은 Source 행 잠금 뒤 실행해 동시 수집이 서로 다른
결정을 내리지 않도록 한다.

외부 수집 진입점은 같은 Source 행을 `SKIP LOCKED`로 먼저 선점한다. 이미
수집 중이면 `SourceAcquisitionInProgressError`로 즉시 종료하며 Provider를
호출하지 않는다. 호출자는 외부 수집이 끝날 때까지 잠금을 얻은 DB transaction을
유지하고, 반환 또는 예외 뒤 commit·rollback한다.

Provider 수집 실패는 `SourceFailureCode`만 `failure_code`로 기록하고 Provider의
본문이나 오류 문자열은 저장하지 않는다. Parser 검증 실패는
`PARSER_VALIDATION_FAILED`, 승인된 거부 한도 초과는
`REJECTION_LIMIT_EXCEEDED`로 기록한다. 세 경로 모두 `snapshot_id=null`인
`FAILED` Run으로 끝나며, Parser 단계까지 확보한 원본은 접근 통제 Artifact 참조로
연결한다. 실패 Run의 생성과 Artifact 참조 저장은 호출자의 같은 DB transaction에
포함된다.

로컬 Artifact 저장은 DB transaction보다 먼저 완료된다. DB rollback은 이미
생성된 내용 주소 객체를 삭제하지 않으며, 참조되지 않은 객체의 보존·정리는 위
후속 운영 정책에서 결정한다. 동일 checksum 객체는 재수집 때 안전하게 재사용한다.

S3 adapter도 DB transaction보다 먼저 조건부 객체 생성을 완료한다. rollback이나
실패 뒤 객체를 자동 삭제하지 않으며, 승인된 보존·정리 정책이 확정되기 전에는
수명주기 규칙 또는 정리 Worker를 구성하지 않는다. REJECTS 보존 기간과 승인 코드
목록도 같은 정책 결정 전에는 운영 활성화 조건을 충족한 것으로 보지 않는다.

Source Artifact runtime 저장소는 기본 `DISABLED`다. `LOCAL_PRIVATE`는 전용 root,
`S3_PRIVATE`는 bucket과 서버 측 암호화 방식을 명시해야 factory가 저장소를 생성한다. S3 access key와
secret key는 Worker `Config`, DB, 로그에 넣지 않고 AWS SDK의 실행 역할·Web
Identity·표준 환경 credential provider chain으로 주입한다. Catalog 적재와 함께
이 factory를 실제 Source 수집 실행에 연결하는 작업은 #166 Runtime 범위다.

DUR·환자용 복약정보의 기존 차단 상태는 유지한다.
평가 Runner 전체 완료를 Parser 단위 작업의 선행조건으로 추가하지 않는다.

## 단위 테스트 명령

```bash
uv run pytest ai_worker/tests/rag/source_ingestion -q
uv run pytest tests/integration/rag/test_source_snapshot_lifecycle.py -q
```

## Worker 이미지 smoke 명령

Worker 설정의 필수 환경변수를 주입하고, PostgreSQL과 같은 Docker network에서
다음 진입점을 실행한다. 이 검증은 고정된 존재하지 않는 Operation ID로
`rag_source_snapshot`을 조회하므로 Source·Snapshot 데이터를 변경하지 않는다.

```bash
docker build -f ai_worker/Dockerfile \
  -t ah-05-04-source-snapshot-smoke:165 .

docker run --rm \
  --network <compose-network> \
  --env-file <worker-env-file> \
  -e DB_HOST=<postgres-service-name> \
  -e STORAGE_DIR=/app/uploads/medical_documents \
  ah-05-04-source-snapshot-smoke:165 \
  uv run --no-sync python -m \
  ai_worker.tasks.rag.source_ingestion.worker_image_smoke
```

성공 출력:

```text
PASS source snapshot adapter import and database connection
```

로컬 검증 환경 파일에는 `STORAGE_DIR`이 없어 smoke 명령에서 비민감 경로를
명시했다. Source DB adapter 검증에는 영향을 주지 않으며, Worker 배포 환경은
기존 OCR runtime 조립 범위에서 필수 저장 경로를 계속 주입해야 한다.
