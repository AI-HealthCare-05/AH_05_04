# #165 Source 수집 구현 진행 기록

## 현재 검증 상태

- Source ingestion 단위 테스트: 199 passed
- AI Worker 전체 테스트: 1882 passed, 8 skipped
- PostgreSQL Snapshot lifecycle 통합 테스트: 4 passed
- Source/Catalog·Artifact Migration 테스트: 9 passed
- 전체 Migration 테스트: 48 passed
- Backend 전체 테스트: 966 passed, 2 skipped
- Mypy: 414개 핵심 소스 파일 통과
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
- Source·Endpoint·Operation exact-match 조회와 Operation 행 잠금 경계 추가
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
- 같은 Operation의 lifecycle 판단 전 행 잠금
- 신규 Snapshot 후보 생성과 `PASSED` 검증·성공 Run 기록
- `NO_CHANGE`, `A → B → A`, 동일 Source version 충돌 판단과 append-only 이력
- Snapshot 후보를 `PENDING`으로 유지해 승인과 Runtime 활성화가 자동으로 일어나지 않는 경계
- 검증 상태의 `PENDING → CURRENT`, 기존 `CURRENT → STALE`와 이전 Snapshot 복원
- 실제 PostgreSQL transaction rollback과 같은 Operation의 동시 판단 직렬화

## 남은 범위

- S3 계열 등 외부 Object Storage를 사용할 경우의 운영 어댑터와 credential 주입
- DB rollback 뒤 참조되지 않은 내용 주소 객체의 보존·정리 정책
- REJECTS Artifact 종류와 보존 정책 확정
- Catalog 적재와 Runtime Bundle 활성화 연결

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
Operation의 판단과 저장은 Operation 행 잠금 뒤 실행해 동시 수집이 서로 다른
결정을 내리지 않도록 한다.

DUR·환자용 복약정보의 기존 차단 상태는 유지한다.
평가 Runner 전체 완료를 Parser 단위 작업의 선행조건으로 추가하지 않는다.

## 단위 테스트 명령

```bash
uv run pytest ai_worker/tests/rag/source_ingestion -q
uv run pytest tests/integration/rag/test_source_snapshot_lifecycle.py -q
```
