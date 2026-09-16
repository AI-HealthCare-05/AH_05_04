# PD-633 — Guide·Chat 피드백 수집과 합성 평가셋 연결

- 상태: **Proposed / 구현·검증 중**. 작업 대화에서 아래 운영안을 채택했다. 책임 리뷰·병합·실사용 공개 승인은 별도다.
- 근거: [Issue #633](https://github.com/AI-HealthCare-05/AH_05_04/issues/633),
  [선행 #581](https://github.com/AI-HealthCare-05/AH_05_04/issues/581).
- 구현: 송은영 (`@phina-io`) — DB·API·Security, 권가빈 (`@hazelnutflavoured`) — UX·Privacy·Gold·평가.
- 단일 책임 리뷰어: 정현우 (`@ceohwj`) — Backend·Frontend 소비 계약, AI/RAG·의료 안전·개선 증빙.
- 전문 의견: 남한솔 — Frontend 연결, 송은영 — 접근·보존·삭제 통제, 권가빈 — 고지·이용 목적.
  추가 필수 PR 리뷰어로 지정하지 않는다. 아래 전달 의견은 Frontend·Backend 설계 확인이며
  Privacy 운영 정책 승인이나 책임 리뷰어의 최종 승인을 대신하지 않는다.

## 전달받은 협의 결과

출처는 사용자가 이 작업 대화에 전달한 팀 메시지다. 원문 메시지 링크와 날짜는 제공되지 않았으며,
아래 시각은 메시지에 표시된 시각이다. GitHub 승인으로 표시하지 않는다.

- 남한솔 11:53: 완료 Guide 및 ASSISTANT·COMPLETED Chat에만 피드백을 표시하는 최소 UI에 동의했다.
  rating 선택 뒤 선택 의견 입력을 열고, GET·새로고침 복원은 추가하지 않는다.
  같은 target 재제출은 기존 row 갱신, 전송 중 중복 클릭 차단, 성공 후 선택 상태 표시와 평가 변경,
  실패 시 rating/comment 유지 후 재시도에 동의했다. 민감정보 입력 금지 안내와
  원문 comment 직접 편입 금지·검토 후 합성 변환 경계를 확인했다.
- 송은영 12:38: 실제 상태·role과 부모 소유권 chain, 두 테이블, 단일 transaction,
  DB trigger/RLS 미사용 설계에 동의했다. 계정 삭제 시 실제 삭제 검증을 유지한다.
  `UserConsentRepository.set_status()`의 upsert와 PR #602 부모 row lock을 구현 참고로 제안했다.
  재사용 시 동일 값 재전송의 시각 보존·소유권 검증을 별도로 확인한다.
- PD-633은 현재 로컬 `feat/633-guide-chat-feedback` 브랜치의 파일이다.
  develop에서 아직 조회되지 않는 것이 맞다. 구현 PR에 Decision·계약·index를 함께 포함할 예정이며,
  게시·병합 후 이 임시 위치 설명을 실제 PR 근거로 갱신한다.

## 구현 착수 기준선 (4a9a9bfa)

Guide는 `profile_id`, ChatMessage는 `session.profile_id`로 SELF 소유권을 확인할 수 있다.
두 결과는 `generation_status`, `model_name`, `prompt_version`을 가지고 있다.
착수 기준선에는 피드백 모델·route가 없었다. Issue의 예시 `user_id`를 그대로 소유권 기준으로 도입하지 않는다.

#581의 canonical 합성 평가셋은 `chat-v4-conversation-quality-eval-v1`이며 27개 case를 가진다.
기존 `baseline/history`는 같은 prompt의 history 유무 비교다. 프롬프트 개선 전후 비교가 아니다.
별도 blind A/B 설정은 v3/v4 prompt를 고정하지만 실행 상태는 `NOT_RUN`이다.
#581 Issue의 Gold 기대·금지 응답 책임 리뷰 승인도 미완료로 표시돼 있다.

## 제안하는 최소 범위

1. 완료된 Guide와 완료된 ASSISTANT 메시지에만 👍/👎와 선택 의견을 받는다.
2. 대상별 피드백은 1개로 유지하고 동일 POST는 재현, 값 변경은 갱신한다.
   변경 이력·관리자 API·자동 학습·외부 전송·새 queue는 만들지 않는다.
3. 대상 FK로 원본·prompt/model을 역추적하며 원본 처방·대화·응답을 피드백에 복제하지 않는다.
4. 부정 피드백은 승인된 검토자가 제한된 운영 환경에서 검토한다. 자동 export나 LLM 변환은 없다.
5. 사람이 실패 유형만 추출해 새 합성 대화를 작성한다. 저장소에는 실제 식별자와 자유 의견을 반입하지 않는다.
6. 새 버전 평가셋·비교 설정과 review evidence를 함께 만들어 기존 불변 평가셋을 보존한다.

세부 후보는 [피드백 계약 v1](../../contracts/proposed/guide-chat-feedback-v1.md),
검토·평가 연결은 [운영 절차 초안](../../operations/guide-chat-feedback-633.md)을 따른다.

## 구현 전 확인할 결정

- 송은영·남한솔의 설계 동의는 위에 기록했다. 사용자가 아래 운영안을 채택했다. 이를 권가빈·송은영의 실사용 처리 근거 승인으로 확대하지 않는다.
- 권가빈·송은영: 자유 의견 수집 고지·이용 목적·보존 기간·삭제와 검토자 접근 수단은 무엇인가.
  기존 GUIDE/CHAT 동의가 개선 목적 자유 의견 처리까지 승인했다는 근거는 아직 없다.
- 정현우: 신규 Gold 기대·금지 응답 승인과 기존 안전 gate 보존을 포함한 단일 책임 리뷰 범위를 확인한다.

위 Privacy 사항을 확인하기 전에는 자유 의견을 실제 사용자로부터 수집하지 않는다.
보존 기간·운영자 권한·동의 목적 enum을 임의로 확정하지 않는다. 확인 결과와 증빙 링크를 이 Decision에
기록한 뒤 같은 구현 PR에서 migration·DTO/OpenAPI·문서·테스트를 정렬한다.
승인·병합·검증 전에는 Current로 승격하지 않으며 Privacy Production·Track C/F 공개 gate는 유지한다.

## 운영안 채택과 구현 delta — 2026-09-16 작업 대화

사용자가 “응 그렇게 하자”로 추천 운영안을 채택했다.

- 참여는 선택이며 거절해도 서비스 이용에 영향이 없다. 이용 목적은 답변 품질 점검·합성 평가셋·프롬프트 개선이다.
- 각 feedback row는 최초 제출부터 최대 30일 보존한다. 수정은 created_at을 바꾸지 않는다.
  검토 목적 달성 후 불필요하면 조기 삭제하고, 원본 연결 기록도 함께 삭제한다.
- 만료 row가 남아 있는 대상에 새 POST가 오면 만료 row를 삭제하고 신규 제출로 저장한다.
  기존 row를 연장하는 갱신과 구분하며 새 id·created_at을 반환한다.
- 권가빈 주 1회 부정 피드백 검토, 송은영 접근·삭제 통제, 정현우 합성 사례·의료 안전 검토를 운영안으로 삼는다.
- 원문은 외부 AI·다운로드·공용 계정·Git에 전달하지 않는다. 합성 사례와 평가 결과만 장기 보관한다.
- 사용자 직접 삭제를 위해 각 POST와 같은 경로의 DELETE를 추가한다. 본인 target이면 row가 없어도 204,
  타인/없는 target이면 404다. GET은 추가하지 않는다.
- 실사용 고지·처리 근거·감사 접근 수단과 외부 승인은 아직 확보되지 않아 API는 ENV=local에서만 허용한다.
  Frontend도 개발 빌드에서만 표시한다. 다른 환경은 POST/DELETE 모두 404이며 설정으로 우회하지 않는다.
- 30일 만료 삭제 명령은 구현하되 실제 일일 스케줄 배포·운영자 권한 신설은 하지 않는다.
  Local 데모 사용자는 합성 데이터만 쓰고 운영 스케줄·감사·백업 삭제 확인 전 실사용 수집을 열지 않는다.

새 DELETE·Local 404·만료 후 신규 접수는 이 Decision의 구체화 delta다.
회원탈퇴 HTTP API는 현재 없어 부모 계정 삭제 완료를 이번 변경으로 주장하지 않는다.
후속 계정 삭제 구현에서는 부모 리소스·피드백·검토 연결 기록·백업 삭제를 함께 검증해야 한다.
