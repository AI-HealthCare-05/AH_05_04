# #166 MFDS 상세 수집기 합성 검증

- 기준: #483 병합 후 develop `1b34d49d`에서 분리한 `feat/166-mfds-detail-acquisition`.
- 실제 MFDS 호출·인증키 사용·운영 Snapshot 생성은 하지 않았다.
- PostgreSQL 17·Redis 7 전용 테스트 컨테이너, HTTP MockTransport와 합성 레코드만 사용했다.
- 실제 상세 Endpoint Receipt를 조작·생성하지 않았다. 테스트용 Receipt/fixture는 임시 디렉터리에만 생성한다.
- 구현/범위: [상세 수집 계약](../contracts/proposed/post-mvp-1/mfds-detail-acquisition-166.md).

## 집중 검증

- `ai_worker/tests/rag/source_ingestion/test_mfds_detail.py`: 24 passed.
- Source ingestion·Source client 및 기존 MFDS Loader 집중 회귀: 655 passed (위 24건 포함).
- `tests/integration/rag/test_mfds_detail_acquisition.py`: 실제 PostgreSQL 3 passed.

| 검증 경계 | 결과 |
| --- | --- |
| 전체 HTTP 페이지 → raw → canonical bytes | 원문·문자열 순번·같은 원료의 반복 행 보존 |
| 빈 행·잘못된 행·관찰 충돌·중복 | 원문/위치/사유/건수 유지, Snapshot 미생성 |
| 일부 페이지·전체 Receipt 오사용·원문 변조·fixture 변조 | 검증 실패, 부분 인계 거부 |
| 페이지 totalCount 변경·중간 빈 페이지·페이지 불일치 | 실패 처리 |
| 인증 실패·일일 한도·schema 오류 | 안전한 실패 코드, Snapshot 미생성 |
| 페이지 수 상한 | 이미 받은 원문만 보존, canonical 후보/성공 Receipt 없음 |
| Operation 잠금 충돌 | 외부 HTTP 호출 전 중단 |
| 원문 저장 실패 | 안전한 예외 전파, 성공으로 처리하지 않음 |
| source_version과 checksum 불일치 | 원문 연결과 실패 감사, Snapshot 미생성 |
| 실제 DB transaction rollback | Snapshot·Run·artifact DB 연결 모두 rollback |
| 실제 DB Snapshot 재조회 → #477 Loader → Catalog → Candidate | 별도 상세 Snapshot Receipt와 반복 성분 연결 성공 |

마지막 경로의 Product Snapshot 및 승인 검증 포트는 합성 fixture/대역이다.
실제 제품 API 수집, 실제 승인 저장소, 공식 component_order 또는 운영 Candidate 활성화 증거가 아니다.
검사 보고서는 private 수집 bundle sidecar이며 DB Receipt가 아니다. DB에는 원문 artifact와 실패 Run을 연결한다.

## 전체 회귀

전체 CI 스크립트 `scripts/ci/run_test.sh` 종료 코드 0.
Migration 212 passed, 4 skipped. Backend·계약·PostgreSQL 1960 passed, 85 skipped. Redis 통합 24 passed.
전체 스크립트의 Worker lane은 3136 passed, 8 skipped였다. 이후 추가한 실패 경로 3건을 포함해
최종 Worker 전체를 재실행했고 **3139 passed, 8 skipped**로 통과했다.
Ruff check·format check 통과, Mypy 600 source files 통과.
DB 단일 migration head `166f30415263`, 사용자 Trigger·RLS·제거 대상 함수 0개 확인.

집중 검사와 전체 회귀의 건수는 중복되므로 합산하지 않는다.
새 상세 경로의 합성 DB 통합 3건은 전체 스크립트 Backend lane에도 포함됐다.
원격 PR CI·담당 리뷰·실제 수집 결과와 구분한다.
