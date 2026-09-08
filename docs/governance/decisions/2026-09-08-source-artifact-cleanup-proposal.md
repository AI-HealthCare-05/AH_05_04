# Source Artifact 보존·삭제 정책 검토 기록 (#335)

상태: Proposed / PM 의견 반영·통합 검토 대상. 실제 삭제 승인이나 새 Contract Freeze가 아니다.

[가빈님 정책 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5580028895)을 반영해 미참조 객체 생성 후 30일 유예, 전체 참조 0건, 운영 중 참조 객체 보존, 종료 시 일회성 수동 정리, DB·보안 검토 후 PM 배치 승인, append-only 감사와 실패 대상만 재시도를 기록한다. 정기 자동 삭제는 이번 구현 범위에서 제외한다.

[은영님 DB·보안 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5579761899), [현우님 Source 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5579812614)과의 통합 본문은 [Source Artifact·REJECTS 정책](../../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md) 한 곳에서 관리한다. 제안 정책 식별자는 `source-artifact-retention-v1`이다. 최종 승인 후 본문을 targets로 이동하고 index·인계 링크를 갱신한다. Current 구현으로 승격하지 않는다.

정책·배치 승인 권가빈, DB·보안 검토 송은영, Source 검토 정현우, 문서 작성 김지혜. 삭제 실행 역할은 Backend·운영이며 코드 구현은 김지혜가 맡고, 실제 운영 실행자와 종료 후 감사 인계는 #347에서 별도 지정한다.

#323 병합에 대한 추가 정책 blocker가 아니며, 실제 Source Runtime 활성화 승인이 아니다. 자동 삭제·Runtime은 DISABLED로 유지한다. 최소 수동 정리·감사 구현은 [#347 Source Artifact 일회성 수동 정리·감사 구현](https://github.com/AI-HealthCare-05/AH_05_04/issues/347)에 연결했다. #335의 통합 문서 검토 및 #165·#323 인계는 남아 있으므로 이 연결만으로 이슈를 종료하지 않는다.
