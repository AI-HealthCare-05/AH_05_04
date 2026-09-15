# #617 Plan 조회·완료·취소 검증

- 구현: 권가빈. 단일 책임 리뷰어: 김지혜.
- 기준 develop: `1ebac025`. 변경 계약: [lifecycle v1](../contracts/proposed/track-c-plan-lifecycle-617.md).
- 테스트는 전용 PostgreSQL 17/pgvector 임시 DB와 합성 데이터만 사용한다.
- 실행 결과는 최종 검증 후 아래에 기록한다.

## 집중 시나리오

- 기존 생성 응답과 snapshot 불변, 저장 상태 GET 및 no-store
- ACTIVE 단일 완료/취소, 종료 상태 변경·재활성화 거부
- strict confirmed=true, extra/missing field 거부, 인증·SELF 미존재/타인 동일 404
- 동일 key replay·다른 body 충돌, snapshot 저장 실패 시 상태·시각·멱등 기록 rollback
- Check-in/Safety 자동 취소 후 완료 거부, ROUTINE Safety/Barrier 정정 후 stale 완료 차단·사용자 취소 허용
- 서로 다른 DB session의 동시 완료/완료·완료/취소·동일 key, Check-in/Safety 정정 경합

## 실행 결과

검증 진행 중. 책임 리뷰 승인·Frontend 통합·실제 사용자 공개 완료를 의미하지 않는다.
Follow-up·실기기/브라우저 E2E·의료 규칙/Provider live 평가·배포는 이번 범위 밖이다.
