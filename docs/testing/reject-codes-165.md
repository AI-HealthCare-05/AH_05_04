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
