# PD-192: 확정 범위의 C1 저장 구체화

- 상태: Proposed — 물리 저장 구조의 PR 리뷰 대상. 담당자 신규 승인으로 표시하지 않음
- 구현: 김지혜, 담당 리뷰어: 송은영
- 범위: #192 / Draft PR #310

## 기존 합의와 근거

- [PM #192 답변](https://github.com/AI-HealthCare-05/AH_05_04/issues/192#issuecomment-5566098431):
  Barrier당 ACTIVE Plan 하나, support_code/rule_version/copy_version/action_config_snapshot 보존,
  Plan당 follow-up 현재값 하나와 정정 audit. Handler별 config 상세·schema version은 미확정.
- [Backend #192 답변](https://github.com/AI-HealthCare-05/AH_05_04/issues/192#issuecomment-5566172552):
  Check-in 현재 row 정정과 audit 구조, 구조화 증상 배열, Safety/Barrier 이력 보존 검토,
  데이터가 있는 downgrade 차단 방향.
- [기존 Draft 설계](../../designs/jye-rookie/issue-192-track-c-storage-implementation-plan.md)
- [승인 목표](../../contracts/targets/post-mvp-1/checkin-v1.md)

#199~#201은 병합되어 Check-in 부모 미구현 차단은 해소됐다. 부모에는 profile_id 직접 컬럼이
없으므로 기존 SELF 부모 chain을 사용한다. 아래 물리화 선택은 기존 합의 전체의 재승인이 아니라
이번 PR에서 검토할 구현안이다.

## 구체화

1. Safety·Barrier는 Check-in revision 안에서 자체 revision을 가진다. 현재 Check-in revision과
   이력 snapshot을 FK로 묶지 않아 Check-in 정정을 방해하지 않는다.
2. Barrier가 근거 Safety와 다른 Check-in/당시 revision을 가리키지 못하도록 복합 FK를 둔다.
3. Plan의 ACTIVE partial unique와 follow-up 현재값/audit 테이블을 분리한다.
4. 소유권 조회를 Python repository에 둔다. 전체 lifecycle과 쓰기는 #193~#195에서 같은
   transaction으로 연결하며 C1에서 의료 판단·Handler 실행을 만들지 않는다.
5. 미승인 HandlerConfig 테이블·필수 JSON 키·schema version·운영 seed는 보류한다.

기존 단일 Check-in 현재값에 모든 C 데이터를 묶으면 과거 revision 정정에 따른 이력 보존을
표현할 수 없다. 5개 테이블은 서로 다른 이력/현재값 역할을 저장하기 위한 최소 분리다.
추가 서비스 추상화나 DB 내부 업무 로직은 도입하지 않는다.

필드·제약·미연결 조건은 [저장 계약](../../contracts/proposed/track-c-storage-v1.md)을 따른다.
공개 API·HTTP 오류·동기 멱등 계약은 변경하지 않는다.
