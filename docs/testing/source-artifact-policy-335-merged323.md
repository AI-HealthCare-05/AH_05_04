# #335 / PR #348 — #323 병합 코드 재조사

- 실행일: 2026-09-08
- 조사·테스트 기준: `2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd` (#323 squash merge)
- 최종 PR head: `61b1adeab9f5f774d056a95169e50aa7328a21a7`
- 병합 시각: 2026-09-08 06:38:52 UTC (GitHub PR metadata 확인)
- 정책 문서: [Source Artifact 보존·정리 변경안](../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md)

## 코드 재조사

위 병합 커밋의 별도 checkout에서 persistence의 파일 선저장→DB 저장 순서,
Local/S3의 checksum key·객체 재사용, Artifact append-only trigger 및 비unique 객체 index,
put_verified만 제공하는 저장 포트를 다시 확인했다. 기존 조사 결론은 유지된다.

기존 기준 b18d5cd 이후 추가된 ingestion 상태 CHECK는 FAILED의 Snapshot NULL과
NO_CHANGE의 Snapshot 필수를 강제한다. Runtime 권한 migration 테스트는 수동 GRANT
문자열 대신 실제 configure-app-role.sql의 provisioning 질의를 사용하도록 보강되었다.
이는 코드 조사이며 아래 단위 테스트가 PostgreSQL 권한·제약까지 검증한 것은 아니다.

## 재실행 결과

병합 커밋 checkout의 루트에서 Python 3.13 테스트 가상환경을 사용했다.

```bash
PYTHONPATH=backend:. python -m pytest ai_worker/tests/rag/source_ingestion/test_local_private_artifact_store.py ai_worker/tests/rag/source_ingestion/test_s3_private_artifact_store.py ai_worker/tests/rag/source_ingestion/test_source_artifact_store_factory.py ai_worker/tests/rag/source_ingestion/test_acquire.py -q
# 45 passed
PYTHONPATH=backend:. python -m pytest ai_worker/tests/rag/source_ingestion -q
# 272 passed (위 45건 포함)
```

모의 S3·합성 파일을 사용했다. 운영 객체·IAM·Object Lock·S3 lifecycle은 조회하지 않았다.
PostgreSQL migration/통합 테스트 및 실제 파일·DB rollback 결합 검증은 재실행하지 않았다.
cleanup 구현과 Runtime 활성화는 이번 정책 문서의 완료 범위가 아니다.

## #348 브랜치 동기화

최신 develop `bd6b4d6bc2d2a04d24bd88012dbaea91ee1aa3fb`를 충돌 없이 병합했다.
Alembic graph는 `165f90716263` 단일 head다. 이는 migration 적용 실행과 구분한다.
PR의 develop 대비 변경은 #335 정책 문서와 이번 조사 증빙에 한정하는지 확인한다.
