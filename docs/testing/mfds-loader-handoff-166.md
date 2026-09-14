# #166 D-04 MFDS Loader 인계 검증

- 날짜: 2026-09-13
- 작업 브랜치: `feat/166-d04-mfds-loader`, 기준 develop `f56a632`
- 상태: 로컬 합성 검사와 원격 CI·담당 리뷰 증빙을 별도 기록. 실제 MFDS 수집·운영 적용 증빙은 아니다.
- 설계: [D-04 Loader 인계](../designs/jye-rookie/issue-166-d04-loader-handoff.md)
- 계약: [관찰 출처·v2/v3](../contracts/proposed/post-mvp-1/catalog-db-integration-v2.md)

## 환경과 경계

전용 PostgreSQL 17·Redis 7, 임시 test DB 및 합성 Source/Snapshot/승인 포트를 사용한다.
개인 API 키·실제 처방전·운영 DB·외부 MFDS 호출은 사용하지 않는다.
실제 SqlAlchemy Source Receipt 조회와 Catalog Repository 저장·복원을 검증하지만
합성 승인 verifier를 운영 승인 저장소로 해석하지 않는다.

## 주요 증빙

- `ai_worker/tests/rag/catalog/test_mfds_component.py`: 원문 키·원료·함량 보존, 빈 행·부분 누락·
  충돌·중복·입력 순열, 명시적 순서만 허용, 이름만으로 코드 병합하지 않는 경계.
- `ai_worker/tests/rag/catalog/test_mfds_loader.py`: 출처·제품 범위·원료 매핑·순서·Receipt·checksum·
  canonical JSON 불일치 시 승인 조회/저장 전 거부. 불완전한 입력의 부분 Catalog 금지.
  관찰 원문 키와 개별 원문 필드의 일치 검사.
- `tests/integration/rag/test_catalog_storage_roundtrip.py`의
  `test_mfds_loader_preserves_groups_sources_and_candidate_handoff`: 서로 다른 제품/상세 Snapshot,
  반복 원료·복수 총량 그룹의 Loader → 실제 DB → 복원 → Candidate 인계. 동일 입력 Set 재사용,
  새 상세 Snapshot 저장 후 과거 Set 불변, 손상 artifact 거부, 빈 행 보고서 보존.
  Receipt와 원문을 함께 바꿔 checksum 자체가 맞더라도 실제 DB Snapshot과 다르면 저장 거부.
- 기존 DB round-trip과 golden fixture: 비관찰 v2 bytes·digest·승인/출처 인계의 호환성,
  read-back 손상 거부·실패 rollback·Writer 권한 분리를 함께 회귀 검증.
- `tests/migration/test_component_observation_sources_migration.py`: 기존 행/참조의 왕복 보존,
  UUID CHAR(36) 타입 일치, 관찰 payload가 있는 downgrade의 손실 차단.
- 기존 migration 테스트의 합성 seed는 과거 스키마 및 새 필수 참조 열을 모두 지원한다.
  적용된 migration 파일이나 과거 데이터의 의미를 변경하지 않는다.

## 검사 결과

최종 코드의 전체 CI 스크립트 실행 결과:

| 검사 | 결과 |
| --- | --- |
| Migration | 212 passed, 4 skipped |
| Backend·Contract·PostgreSQL 통합 | 1947 passed, 85 skipped |
| Redis 통합 | 24 passed |
| Worker (RAG·OCR·Evaluation 포함) | 3109 passed, 8 skipped |
| Ruff·format | 통과 |
| Mypy | 595 source files 통과 |
| Migration head·금지 DB 로직·보호 테이블 쓰기·테스트 분류 | 통과 |

`scripts/ci/run_test.sh` 전체 통과(exit 0). 통합 커버리지 92%.
Ruff·format·Mypy 및 `git diff --check`도 통과했다.
전용 DB head는 `166f30415263`이며 사용자 Trigger·RLS·제거 대상 함수는 0개다.
집중 Loader 검사 18건 및 DB 왕복 검사는 전체 검사와 중복되므로 합산하지 않는다.

로컬 검사에는 인증 없는 임시 Docker 설정을 사용해 개인 Docker 인증 도우미를 우회했다.
임시 env는 Worker 단위 검사의 Redis 기본 주소를 유지하고 실제 통합 연결 주소/포트는
CI 스크립트에서 전용 Compose 서비스로 지정했다. 테스트 전용 Redis 비밀번호도 별도로 연결했다.
저장소 env 예시·개인 env·Docker 설정은 수정하지 않았다.

## 원격 CI·담당 리뷰 증빙 — 2026-09-13 확인

- 검토·CI 대상 HEAD: `c58f09686d3a72567793ee237abeb500ab04e710`.
- [원격 CI 실행](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/34752583896): completed / success.
  lint, test-backend, test-worker, test-inventory, test-migration, frontend, test 모두 success(7/7).
- [정현우 승인 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/477#pullrequestreview-5190458759): MFDS 매핑의 의미·한계, 반복 성분·총량 그룹, 출처 분리, Candidate 인계 확인.
- 리뷰의 D-03 WATCH는 결정 문서에 공개 리뷰 URL과 최초 Discord 합의 링크를 연결해 보완했다.
- 위 로컬 검사 수치와 기준 develop `f56a632`는 당시 실행 기록으로 보존한다. 최신 HEAD에서
  로컬 전체 검사를 재실행한 것으로 바꾸지 않는다. 이후 문서 커밋의 CI는 해당 커밋에서 별도 확인한다.
- 이 문서 보완에서는 실행 코드·migration을 변경하지 않았으며 전체 실행 테스트를 재실행하지 않았다.

## 실입력 한계

주성분 상세 Operation 자동 수집·Raw Artifact 생성·실제 Snapshot 생산은 이번 검증에 포함하지 않는다.
MFDS 공식 MTRAL_CODE 안정성이나 TAMT_SEQ/MTRAL_SN 의미를 합성 테스트로 입증하지 않는다.
명시적 순서 계약을 입력받고 그 버전·원문 그룹을 보존한다. 실제 승인·철회·감사 저장소와
Runtime 공개 조건은 후속이다. D-03은 현재 P0 Crosswalk 소비 경로가 없어 별도로 이관했다.
