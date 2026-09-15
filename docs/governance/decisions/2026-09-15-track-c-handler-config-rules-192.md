# PD-192-2 — Track C HandlerConfig 운영 Rule·Copy 제품 승인

- 날짜: 2026-09-15
- 상태: **제품 승인 / 합성 데이터 기반 비공개 데모 승인 / 외부 공개 승인 아님**
- 제품 승인: 권가빈 (`@hazelnutflavoured`)
- 기술 리뷰: 송은영 (`@phina-io`)
- 화면 소비 확인: 남한솔
- 승인 기록: [GitHub Issue #192 댓글](https://github.com/AI-HealthCare-05/AH_05_04/issues/192#issuecomment-5674986826)
- 제품 참고: [Personalised Adherence Support 상세 v1.3](https://app.notion.com/p/3c0233603e2780c29411d8d271ad60fb)

## 결정

1. 내부 활성 Rule은 `track-c-support-rule-2026-09-15.1`, 한국어 Copy는
   `track-c-support-copy-ko-2026-09-15.1`로 발행한다.
2. 기존 6개 `support_code`, Barrier 대응과 priority는 승인된 Check-in target을 유지한다.
3. 지원별 `rationale_code`는 다음과 같다.
   - `ROUTINE_REMINDER_SETUP_AVAILABLE`
   - `ROUTINE_OR_TRAVEL_GUIDANCE_AVAILABLE`
   - `ROUTINE_INSTRUCTION_REVIEW_AVAILABLE`
   - `ROUTINE_PURPOSE_REVIEW_AVAILABLE`
   - `ROUTINE_MEDICATION_CONCERN_GUIDANCE_AVAILABLE`
   - `ROUTINE_ACCESS_SUPPORT_AVAILABLE`
4. Copy는 제목·본문과 명시적 사용자 확인 문구를 가지며 추가 직접 입력을 요구하지 않는다.
5. `REMINDER_SETUP`만 기존 복약 일정 확인·설정 화면으로 연결하고 사용자 확인 전 자동 적용하지 않는다.
6. 기존 무버전 또는 `{}` snapshot은 임의 backfill하지 않고 복원 거부 상태로 보존한다.
7. 위 Rule·Copy와 합성 fixture를 사용하는 비공개 개발·데모 환경의 #192 시연을 승인한다.

## 경계

이번 결정은 #192의 내부 Rule·Copy 파일, 승인 allowlist와 엄격 로더에만 적용한다. 공개 API, Support 선택,
ActionPlan 완료, 사용자 표시 오류와 이행 API는 #194 계약에서 연결한다. Safety·Barrier API는 #193,
Check-in 정정 무효화는 #195 범위다.

연결된 Notion 구현 상세 설계 v2는 Draft·재승인 대기 상태이므로 확장 Handler, RAG·LLM, Support Offer,
Content·Prompt·Model·Source snapshot과 Follow-up 5종을 이번 결정에 포함하지 않는다.

외부 의료·Privacy·Safety 승인과 배포 환경 검증은 별도 게이트다. 완료 전 `PUBLIC_TRACK_C=false`를 유지한다.
이는 합성 데이터 기반 비공개 데모 실행을 막지 않는다.
