# #192 Track C 운영 규칙 승인 기록 2026-09-15.1

- 상태: **제품 승인 / 합성 데이터 기반 비공개 데모 승인 / 외부 공개 승인 아님**
- 범위: 6개 최소 안내형 Support의 rule/copy와 승격 기록
- 제품 참고: [Personalised Adherence Support 상세 v1.3](https://app.notion.com/p/3c0233603e2780c29411d8d271ad60fb)
- 저장 계약: [#192 HandlerConfig 구체안](../../track-c-handler-config-192.md)
- 제품 결정: [PD-192-2](../../../../governance/decisions/2026-09-15-track-c-handler-config-rules-192.md)

## 파일

- `backend/app/config/track_c/support-rules/track-c-support-rule-2026-09-15.1.json`: Barrier 대응,
  priority, copy 참조, rationale와 허용 parameter
- `backend/app/config/track_c/support-copy/track-c-support-copy-ko-2026-09-15.1.json`: 한국어 제목·본문·사용자 확인 단계

두 파일은 제품 승인 뒤 운영 로딩 경로와 명시적 allowlist에 연결했다. 파일 존재만으로 다른 버전이 활성화되지는
않으며, `load_active_handler_config`와 `load_active_support_copy_catalog`은 위 버전만 읽는다.

## 적용한 범위

- Notion v1.3의 비판단적 표현, 사용자 선택권, Safety 우선, 처방 변경 금지 원칙을 반영한다.
- 추가 직접 입력 없이 안내와 명시적 확인 단계만 제공한다.
- `REMINDER_SETUP`만 기존 복약 일정 확인·설정 화면 진입을 전제로 한다.
- 시간·횟수·용량·식사 조건을 생성하거나 변경하지 않는다.
- 약국 재고·가격·지원 가능성을 추정하거나 보장하지 않는다.

## 포함하지 않은 범위

연결된 “구현 상세 설계 v2”는 Draft·재승인 대기 상태이므로 확장 Handler, RAG·LLM,
Support Offer, Content·Prompt·Model·Source snapshot과 Follow-up 5종을 이 승인 자료에 반영하지 않는다.
이는 #193~#195 및 별도 승인 계약 범위다.

## 승인·승격 체크

- [x] 권가빈: 6개 `rationale_code`, 제목·본문·확인 문구 승인
- [ ] 송은영: 버전명·기존 무버전/`{}` snapshot 처리 기준 검토
- [ ] 남한솔: 확인 단계와 `REMINDER_SETUP` 화면 연결 검토
- [x] 승인 기록과 불변 version 발급
- [x] 운영 경로 및 활성 allowlist 연결
- [x] Rule·Copy 교차 참조와 엄격 로더 테스트

합성 fixture 기반 비공개 데모는 허용한다. 기술·화면 리뷰와 외부 게이트 전에는 `PUBLIC_TRACK_C=false`를
유지하고 실제 사용자 데이터 적용이나 기존 데이터 backfill을 수행하지 않는다.
