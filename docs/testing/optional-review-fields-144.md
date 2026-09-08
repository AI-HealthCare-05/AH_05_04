# #144 빈 검수 필드 — 3·4단계 증빙

- 상태: 작업 브랜치 구조화 및 DB·PATCH 통합 검증 완료. DOC-03·담당 리뷰·CI 대기.
- 관련: [구현 계획](../designs/issue-144-optional-review-fields-implementation-plan.md), [변경안](../contracts/proposed/ocr-empty-review-fields-144.md)
- 기반: `7d4510f5f6e1f9f70fd25ce4bff30127fda41955`, 계약 초안 커밋 `2d9bcdf`
- 실행일: 2026-09-08
- 검증 대상: 이 문서와 함께 커밋하는 3단계 코드·테스트 변경분. 이후 변경은 재검증한다.

## 결과

| 범위 | 결과 |
| --- | --- |
| backend/app/tests/ocr_ai | 59 passed |
| 규칙 structurer 및 구조화 adapter | 99 passed |
| Worker CLOVA provider adapter | 21 passed |
| Ruff: ocr_runtime 및 변경 validator·테스트 | 통과 |
| Mypy: ocr_runtime 및 validator | 7 source files 통과 |
| 변경 Python 파일 포맷·git diff --check | 통과 |

누락·grounding 실패 시 네 metadata null, 다른 약품 행 근거 거부, 정상값 보존,
부분 인식 행 유지, 동일 입력 재실행의 결과·순서 일치, 중복 identity 없음,
헤더 없는 입력·안내문에서 약품 행 비생성을 확인했다.
기존 생략 기대 8건은 빈 필드 정책으로 갱신했고 신규 매개변수 사례 12건을 추가했다.

## 재현 범위

저장소 루트에서 테스트 환경의 Python/pytest를 사용한다. 로컬 실행은 Python 3.13
테스트 가상환경을 사용했다. 아래 DB 설정은 import에 필요한 합성 설정이며 연결하지 않는다.

```bash
export DB_HOST=127.0.0.1 DB_USER=synthetic DB_PASSWORD=synthetic DB_NAME=test
export PYTHONPATH=backend:.
python -m pytest --confcutdir=backend/app/tests/ocr_ai backend/app/tests/ocr_ai -q
python -m pytest --confcutdir=backend/app/tests/ocr backend/app/tests/ocr/test_prescription_ocr_structurer.py backend/app/tests/ocr/test_structurer.py -q
python -m pytest ai_worker/tests/ocr/test_clova_ocr_provider_adapter.py -q
ruff check ocr_runtime backend/app/services/ocr_ai/validator.py backend/app/tests/ocr_ai/test_validator.py backend/app/tests/ocr/test_prescription_ocr_structurer.py
MYPYPATH=backend:. python -m mypy ocr_runtime backend/app/services/ocr_ai/validator.py
```

`--confcutdir`는 이 순수 구조화 테스트에서 상위 Backend의 DB drop/create autouse
fixture를 로드하지 않기 위한 설정이다. DB 테스트를 대체하거나 공식 CI 전체가
통과했다는 의미가 아니다. 외부 Provider는 호출하지 않았으며 원문 데이터는 추가하지 않았다.
최초 수집 시 필수 DB 환경설정 누락 오류가 있어 위 합성 설정으로 재실행했다.

## 3단계 종료 시 남았던 검증 (아래 4단계 결과 참고)

- 격리 PostgreSQL의 실제 migration·unique·confirmation 제약
- 빈 필드 저장→field_id 조회→값 PATCH·Optional null 확정
- 기존 소유권·Job 상태·처방 확정 후 수정 차단 및 저장 실패 transaction
- DOC-03 소비 확인, 담당 리뷰, current 계약 통합 및 CI


## 4단계 — PostgreSQL·PATCH 검증 (2026-09-08)

3단계 코드 `ef5abb6` 위에 이 문서와 함께 커밋하는 통합 테스트를 추가했다.
기존 서비스 PostgreSQL과 별도 컨테이너(PostgreSQL 16, loopback 55444, 합성 test DB)를
사용했다. 실제 환자 데이터와 운영 DB는 사용하지 않았다.

| 검증 | 결과 |
| --- | --- |
| 처방 확정 API·OCR repository·처방 validation | 53 passed |
| Worker OCR PostgreSQL persistence | 3 passed |
| Alembic upgrade head | 성공, `165f90716263` |
| 실제 extracted_field unique·confirmation CHECK 조회 | 모델과 정책 일치 |
| 변경 테스트 Ruff·포맷·공백 검사 | 통과 |

두 구조화 경로에서 약품명만 있는 입력으로 빈 필드를 실제 생성했다.
기존 OcrRepository로 저장하고 GET에서 8개 field_id(문서 날짜 포함)를 확인한 뒤,
함량·단위·TIMING을 값 입력 또는 null로 PATCH하고 처방 확정까지 검증했다.
중복 identity 삽입 실패 후 savepoint rollback으로 기존 field_id가 보존되는지 확인했다.
기존 미검수·필수값·다른 사용자 처방·확정 후 변경 거부 회귀도 함께 실행했다.

Worker 테스트는 실제 PostgreSQL의 전용 최소 스키마에서 함량·단위 null을 보존하고
외부 transaction commit 전에는 결과가 보이지 않으며 commit 후 보이는지 확인했다.
Backend 테스트는 기존 fixture의 모델 create_all 스키마를 사용한다. 별도로 빈 DB에
실제 Alembic을 적용하고 `uq_extracted_field_identity` 및
`chk_field_confirmation_fields` 등 6개 제약을 조회하여 스키마 차이를 점검했다.
이 결과에 따라 신규 migration과 API 구현 변경은 추가하지 않았다.

```bash
export DB_HOST=127.0.0.1 DB_PORT=55444 DB_EXPOSE_PORT=55444
export DB_USER=synthetic DB_PASSWORD=synthetic DB_NAME=test PYTHONPATH=backend:.
python -m pytest backend/app/tests/ocr/test_prescription_confirmation_api.py backend/app/tests/ocr/test_ocr_repository.py backend/app/tests/ocr/test_prescription_confirmation_validation.py -q
python -m alembic -c backend/alembic.ini upgrade head
python -m pytest tests/integration/test_worker_ocr_persistence.py -q
```

위 순서는 격리 DB에서만 실행한다. Backend fixture가 스키마를 drop/create하므로
migration 검증과 동시에 실행하지 않는다. 신규 통합 사례는 Backend 4개와 Worker 2개다.
DOC-03 실제 화면 확인·담당 리뷰·CI·current 계약 통합은 5단계에 남아 있다.
