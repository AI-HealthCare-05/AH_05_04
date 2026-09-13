# Source Artifact 보존·삭제 정책 검토 기록 (#335)

상태: Proposed / PM 의견 반영·통합 검토 대상. 실제 삭제 승인이나 새 Contract Freeze가 아니다.

[가빈님 정책 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5580028895)을 반영해 미참조 객체 생성 후 30일 유예, 전체 참조 0건, 운영 중 참조 객체 보존, 종료 시 일회성 수동 정리, DB·보안 검토 후 PM 배치 승인, append-only 감사와 실패 대상만 재시도를 기록한다. 정기 자동 삭제는 이번 구현 범위에서 제외한다.

[은영님 DB·보안 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5579761899), [현우님 Source 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5579812614)과의 통합 본문은 [Source Artifact·REJECTS 정책](../../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md) 한 곳에서 관리한다. 제안 정책 식별자는 `source-artifact-retention-v1`이다. 최종 승인 후 본문을 targets로 이동하고 index·인계 링크를 갱신한다. Current 구현으로 승격하지 않는다.

정책·배치 승인 권가빈, DB·보안 검토 송은영, Source 검토 정현우, 문서 작성 김지혜. 삭제 실행 역할은 Backend·운영이며 코드 구현은 김지혜가 맡고, 실제 운영 실행자와 종료 후 감사 인계는 #347에서 별도 지정한다.

#323 병합에 대한 추가 정책 blocker가 아니며, 실제 Source Runtime 활성화 승인이 아니다. 자동 삭제·Runtime은 DISABLED로 유지한다. 최소 수동 정리·감사 구현은 [#347 Source Artifact 일회성 수동 정리·감사 구현](https://github.com/AI-HealthCare-05/AH_05_04/issues/347)에 연결했다. #335의 통합 문서 검토 및 #165·#323 인계는 남아 있으므로 이 연결만으로 이슈를 종료하지 않는다.


## PR #363 리뷰에 따른 내부 합성 도구 보완안

가빈님 리뷰의 승인 철회 경합·만료/오기입 재검토 요구와 현우님 리뷰의 downstream 범위 분리
요구를 반영한다. 검토는 append-only revision을 추가하고 최신 역할별 행만 사용한다.
철회된 배치의 해제는 추가하지 않는다. 승인 변경과 실행은 배치별 잠금으로 직렬화하며
경합 요청은 즉시 실패한다. 운영 참조 조회가 구현되지 않은 범위는 명시적으로 차단한다.

구체적인 잠금 순서·재검토·이전 합성 설치 보존 절차는 연결된 Proposed 계약과 runbook에
기록한다. 김지혜가 구현하며 권가빈(정책), 송은영(DB·보안), 정현우(Source)의 PR 검토 대상이다.
현재 운영 정책·Runtime 활성화나 새 공유 Alembic 계약을 확정한 기록은 아니다.


PR #363 가빈님 추가 리뷰에 따라 기존 정책의 DB_SECURITY → PM 승인 순서를 검증하고,
합성 audit는 전용 DB 함수가 등록된 역할·대상·최신 승인 근거와 서버 기록 시각을 채우도록 보완한다.
실행자의 자유형 audit INSERT 권한을 제거한다. 이벤트 결과는 실행자 보고로 구분하며 DB가
파일 삭제를 독립 관측했다고 표현하지 않는다. 구현·검증·인계 문서를 함께 리뷰한다.
