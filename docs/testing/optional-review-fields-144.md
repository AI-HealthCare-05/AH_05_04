# #144 빈 검수 필드 — 3단계 증빙

- 상태: 작업 브랜치 구조화 구현 검증. DB·PATCH 통합 검증 미실행.
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

## 남은 검증

- 격리 PostgreSQL의 실제 migration·unique·confirmation 제약
- 빈 필드 저장→field_id 조회→값 PATCH·Optional null 확정
- 기존 소유권·Job 상태·처방 확정 후 수정 차단 및 저장 실패 transaction
- DOC-03 소비 확인, 담당 리뷰, current 계약 통합 및 CI
