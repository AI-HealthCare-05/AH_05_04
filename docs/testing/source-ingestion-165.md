# #165 Source 수집 구현 진행 기록

## 현재 검증 상태

- Source ingestion 단위 테스트: 166 passed
- 실제 MFDS 호출과 DB 저장은 이번 단위 테스트에 포함하지 않는다.
- Snapshot 생성·승인·Runtime 활성화는 아직 연결하지 않았다.

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

기존 Evaluation serializer는 변경하지 않았다. Source checksum과
Evaluation Manifest hash는 계산 범위와 제외 규칙이 다르므로 각각의
버전과 계약을 유지한다.

실제 MFDS 재수집에서 동일 값의 Unicode 형태가 달라지는 현상이
발생하는지는 후속 실측으로 확인한다. 문제가 확인되면 기존 버전의
결과를 덮어쓰지 않고 새 Source 전용 규칙과 버전을 추가한다.
검색용 파생값과 Resource 경로 정규화는 별도 범위로 다룬다.

## #164 연결 후 남은 범위

- 접근 통제된 저장 계층과 Artifact Key 연결
- 검증 이후에도 같은 원본을 사용하는 불변 저장 경계
- #164에서 승인된 Source/Snapshot 저장 인터페이스와 Worker adapter 연결
- Snapshot 상태 전이와 `NO_CHANGE`, A→B→A, 외부 version 충돌 검증
- 이전 승인 Snapshot으로의 rollback 검증

위 항목은 #164의 Source·Snapshot 모델, migration, 제약과 저장
인터페이스가 확정된 뒤 연결한다. 현재 `ProductIngestionResult`가
저장 경계에 전달할 identity, Endpoint Receipt hash,
Raw Manifest checksum, canonical checksum,
canonicalization spec version과 record count를 제공한다.

PR #291의 `RagSourceSnapshotCreate` 저장 계약에 연결하려면 현재
`ProductIngestionResult`에 없는 다음 6개 값을 #164 저장 adapter 경계에서
확정해 함께 전달해야 한다.

- `source_version`
- `schema_version`
- `parser_version`
- `normalization_version`
- `rejected_record_count`
- `collected_at`

이 값들은 원본 또는 checksum에서 임의로 추론하지 않으며 #164에서 승인된
Source·Snapshot 저장 인터페이스와 수집 실행 provenance를 기준으로 채운다.

DUR·환자용 복약정보의 기존 차단 상태는 유지한다.
평가 Runner 전체 완료를 Parser 단위 작업의 선행조건으로 추가하지 않는다.

## 단위 테스트 명령

```bash
uv run pytest ai_worker/tests/rag/source_ingestion -q
```
