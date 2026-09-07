# #165 Source 수집 구현 진행 기록

## 현재 검증 상태

- Source ingestion 단위 테스트: 100 passed
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

## 정규화 규칙 합의 대기

현재 코드는 은영님 의견을 기준으로 checksum용 JSON에 NFC를 적용한다.
현우님은 Source checksum에도 NFC를 적용하지 않는 방향을 제시했다.
두 의견을 조율하기 전까지 현재 구현을 최종 승인된 규칙으로 간주하지 않는다.

추가로 확정할 항목:

- NFC 적용 여부와 적용 대상
- 객체 key 정렬 기준
- 허용 숫자 타입과 정수 범위
- ITEM_SEQ 허용 타입과 정렬 기준
- Operation별 Envelope 제외 경로
- canonicalization_spec_version

기존 Evaluation serializer와의 차이:

| 항목 | 현재 Source 구현 | Evaluation 구현 |
| --- | --- | --- |
| Unicode NFC | checksum용 JSON에 적용 | 적용하지 않음 |
| 객체 key 정렬 | Python 기본 문자열 정렬 | UTF-16 기준 정렬 |
| 정수 | 별도 범위 제한 없음 | 안전 범위 정수만 허용 |
| 실수 | 유한 실수 허용 | 거부 |
| 문자열 앞뒤 공백 | 보존 | 보존 |

Source checksum과 Evaluation Manifest hash의 제외 규칙은 공유하지 않는다.
검색용 파생값과 Resource 경로 정규화는 별도 범위로 다룬다.

## 남은 연결·검증

- Receipt 파일 자체의 hash와 연결된 증빙 무결성 검증
- Operation 응답 계약과 Envelope 제외 경로 연결
- 수집 페이지와 Raw Artifact 목록의 누락·불일치 검증
- 접근 통제된 저장 계층과 Artifact Key 연결
- 검증 이후에도 같은 원본을 사용하는 불변 저장 경계
- 확정된 직렬화 규칙·버전과 추가 고정 테스트 벡터
- #164에서 승인된 Source/Snapshot 저장 인터페이스와 Worker adapter 연결
- Snapshot 상태 전이, NO_CHANGE, A→B→A, 외부 버전 충돌과 rollback

DUR·환자용 복약정보의 기존 차단 상태는 유지한다.
평가 Runner 전체 완료를 Parser 단위 작업의 선행조건으로 추가하지 않는다.

## 단위 테스트 명령

```bash
uv run pytest ai_worker/tests/rag/source_ingestion -q
```

