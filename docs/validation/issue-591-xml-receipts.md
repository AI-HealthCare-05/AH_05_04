# #591 XML Receipt 발급·검증 구현 검증

구현 담당 김지혜, 책임 리뷰어 송은영. 정책·서버 설정 대조는 권가빈.
Base: `b9ae2edf`. 실제 제품 Receipt 발급·서버 실행·DB 등록·READY 변경은 이번 검증에서 수행하지 않았다.

## 변경과 검증 결과

- XML 전용 제품/Endpoint Receipt 발급, acquisition manifest 생성과 원문 기반 전체 재검증.
- 승인된 16개/NN 5개 allowlist, 실제 취득시각·원문 bytes 검증, hash 단방향 결속.
- 기존 XML canonical 함수를 공유하도록 추출. Snapshot 전체 canonical hash 계산은 기존 UTF-16 key ordering canonical serializer를 유지한다. 새 Receipt JSON은 승인된 Python sort_keys 규칙을 사용한다. 둘을 혼동하지 않는다.
- private 0700/0600·owner·symlink·bounded read 검증, O_EXCL 생성·fsync·실패 시 기존 증빙 보존.
- JSON 추가/중복 키, placeholder ref, 문서 scope/순서/size/hash 오류, 재계산한 위조 Endpoint/제품 연결도 원문/승인 입력과 대조하여 거부.
- NN 공식 공백은 PARTIAL_OFFICIAL 원문 증빙으로 보존. #634 materializer의 NN chunk gate를 변경하지 않는다.
- CLI 오류 출력에서 private 경로·원문·외부 exception 메시지 비노출.

실행 결과:

| 검증 | 결과 |
| --- | --- |
| XML Receipt 합성 테스트 | 43개, 전체 RAG 실행에 포함되어 통과 |
| `pytest ai_worker/tests/rag -q` (PYTHONPATH=.) | 2044 passed |
| `ruff check .` | PASS |
| `ruff format . --check` | 1012 files already formatted |
| `mypy backend/app ai_worker` | PASS, 766 source files |
| Python test inventory | fully classified |
| `git diff --check` | PASS |

## 미실행과 한계

- `scripts/ci/run_test.sh` 전체 PostgreSQL/Redis/migration 통합 실행은 이 worktree에 `envs/.local.env`가 없어 미실행. 다른 환경 credential을 복사하거나 운영 DB에 연결하지 않았다. CI의 전체 required checks는 PR에서 별도 확인해야 한다.
- 현재 hash 도구는 입력한 승인 ref/제품명/실행 commit의 외부 진실성을 인증하지 않는다. 김지혜가 승인 원본·제품명 근거·실제 사용 commit을 대조한다. 원문 재조회와 hash 정합성 검증은 수행한다.
- snapshot/operation 등록 여부를 검사하는 DB 도구가 아니며, profile `READY`를 변경하거나 검증 완료로 선언하지 않는다. optional profile 대조도 identity/hash/문서범위/ref에 한정한다.
- 출력 파일 전체는 filesystem transaction이 아니다. 일부 파일 생성 후 실패하면 그 증빙을 보존하고 자동 재발급을 차단한다.
- 실제 53개 XML은 저장소 밖에 그대로 보존되어 있으며 이 PR에는 합성 fixture와 코드·문서만 포함한다.
