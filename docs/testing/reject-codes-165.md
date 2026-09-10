# #165 reject_code 단계별 구현·검증

기준: #436 head c1587f7. 브랜치 feat/165-reject-code-contract.
#436 병합 후 최종 base/head 재검증 필요. #372 변경은 포함하지 않는다.
사용자 지시: 단계별 커밋, 전체 정합성 감사, 푸시·원격 PR 생성 금지. 마지막에 푸시 필요 표시.

1. 기준·브랜치 확인 완료. AGENTS.md·CONTRIBUTING.md·SECURITY.md·privacy-safety.md를 읽음.
2. 코드/버전/Operation 고정 검증.
3. 전체 페이지 2-pass 판정.
4. Run 버전·REJECTS·실패 감사와 migration 연결.
5. 실제 경로·DB·실패 복구·정보 노출 방지 검증.
6. 정합성 감사 및 로컬 PR 본문 준비.

계약: [리뷰안](../contracts/proposed/post-mvp-1/source-reject-codes-v1.md).
은영님 사전 승인 대기를 구현 차단 조건으로 삼지 않으며 승인 완료로 표시하지 않는다.
기존 첨부 MD 내용은 대화에서 읽은 v2를 기준으로 위 계약에 반영했다.

2단계 완료: 버전·Parser 매핑·Operation·코드·위치 검증 20건 통과.
문서 목록과 코드 enum 일치 및 미등록 입력의 안전한 고정 오류를 검사했다.

3단계 완료: 전체 페이지 필수값·타입·중복 2-pass, 모든 중복 행 기록, 원문 비노출 typed 오류 연결.
Source ingestion 회귀 418건 통과. 기존 checksum 오류 문자열 의존 테스트를 고정 안전 오류로 정렬했다.

4단계 완료: 새 orchestration, checksum 전 실패 감사 복원, Run 계약 버전 저장·Receipt 조회,
파일 보존 전/Repository 재검증, identity 오류의 Hard Limit 이전 실패, 기존 partial Snapshot 승인 보호 유지.
신규 실제 DB 경로 10건, 기존 lifecycle 31건, migration·과거 NULL·downgrade·컬럼 권한 3건 통과.
Source 단위 418건, Ruff·format 통과. 신규 revision 165a0b1c2d3e는 #436 merge head 362c3d4e5f60의 자식이다.

5단계 완료: 실패 복구·기존 CURRENT 보존·중복 Artifact 추가 차단·원문 오류 비노출을 검증했다.
동일 위치의 0 패딩 표기도 숫자 위치로 비교해 중복 저장을 차단한다.
통신 실패는 기존 FailedIngestionRunResult와 실패 코드를 유지하며 Parser 실패로 바꾸지 않는다.

검증 결과 (2026-09-11, 작업 전용 PostgreSQL 17):

- 신규 DB 통합 25건 + Source 단위 418건: 443 passed.
- 전체 migration: 180 passed.
- RAG·Evaluation: 2429 passed, 8 skipped. 이 결과에는 Source 단위 등 중복 테스트가 포함된다.
- Writer·프로비저닝·이미지·Backend Source 경계: 25 passed, 정리 DB 미설정으로 57 skipped.
- 위에서 건너뛴 정리 경로는 전용 DB를 설정해 별도 실행: 57 passed.
- 기존 Snapshot lifecycle: 31 passed.
- Ruff, format (704 files), Mypy (548 source files), DB 로직 금지 검사,
  보호 쓰기 경로 검사, Python 테스트 inventory 통과.
- 전용 테스트 DB upgrade head 및 종합 검증 통과:
  165a0b1c2d3e, Trigger/RLS/제거 대상 함수 0개.

`scripts/ci/run_test.sh` 전체 실행은 `envs/.local.env` 부재로 환경 준비 단계에서 중단했다.
위 검증은 별도 전용 DB 환경으로 직접 실행한 결과이며 전체 CI 통과로 표현하지 않는다.
운영·AWS·팀원 DB에는 적용하지 않았고, 원격 CI는 푸시하지 않아 실행하지 않았다.

6단계 완료: 계약·Decision·구현·migration·테스트의 최종 정합성을 확인하고
[로컬 PR 제목·본문 초안](reject-codes-165-pr-draft.md)을 준비했다.

| 완료 조건 | 구현 및 검증 근거 |
| --- | --- |
| 세 코드·버전·Operation 고정 | reject_codes.py, test_reject_codes.py의 문서/enum 대조 |
| 전체 페이지·중복 그룹 전체·원문 유지 | product_rejections.py, test_product_rejections.py 및 DB 통합 |
| 오류 시 Snapshot/CURRENT 보존 | persistence.py, snapshot_lifecycle.py 및 기존 CURRENT 통합 사례 |
| 실제 수집 PK 실패 감사 연결 | 실패 Raw Artifact 재검증·복원, 원래 실패 결과 불변 통합 사례 |
| Run 버전·과거 NULL·최소 권한 | 165a0b1c2d3e migration, 모델/Repository, migration 권한 테스트 |
| 저장 실패·재시도·정리 경로 | 파일/DB 실패 rollback 통합, 보존·삭제 57건 |
| 금지 DB 로직 없음 | 정적 검사 및 최종 head 실DB 검사 모두 통과 |

의도된 한계: 기존 내부 API의 과거 parser 호환성은 유지한다. 새 계약 적용에는 새 orchestration이 필요하다.
#436 최종 병합 상태와 이후 develop 변경은 원격 PR 준비 시 재확인한다.
이번 계약 작업은 #165 전체 완료나 #166 소비 연결 완료를 의미하지 않는다.
로컬 단계별 작업 완료. **푸시 필요.**
