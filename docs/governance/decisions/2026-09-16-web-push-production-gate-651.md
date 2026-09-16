# PD-469-2 — Web Push Production 활성화 게이트

- 상태: 2026-09-16 PR #657 구현 반영. 단계적 확대 실행(#471 연결)은 별도.
- 구현 담당: 송은영 (@phina-io).
- 단일 책임 리뷰어: 권가빈 (@hazelnutflavoured) — Release Gate, 단계별 대상 확대 승인.
- 영향 영역: Backend Web Push 등록·전송 Production 공개 조건.
- 배정 근거: #469 PD-469에서 "Production에서는 enabled 값과 무관하게 차단"으로 확정했던 조건을, #651에서 전용 게이트 플래그로 대체하기로 함.
- 원본 Issue: [#651](https://github.com/AI-HealthCare-05/AH_05_04/issues/651). 선행 [PD-469](2026-09-13-web-push-469.md).

## 문제와 제품 결정

[PD-469](2026-09-13-web-push-469.md)는 #469/#470 구현이 끝나도 Production에서 Web Push를
절대 켤 수 없도록 무조건 차단을 확정했다. #469/#470이 merge된 뒤 이 하드 차단을 안전하게
여는 전용 게이트가 필요해졌다.

1. `WEB_PUSH_PRODUCTION_ENABLED`(기본 false)를 추가한다. Production에서 이 값이 false면
   기존처럼 `WEB_PUSH_ENABLED` 값과 무관하게 등록·전송을 차단한다. true이면 `WEB_PUSH_ENABLED`
   값에 따라 정상 동작한다.
2. 다른 `config.ENV == "production"` 안전장치(이메일 인증 강제 등)는 변경하지 않는다.
3. 이 플래그를 끄면 신규 config/등록 호출은 즉시 거부되지만, 이미 그 값을 읽어 메모리에
   올린 API/scheduler 프로세스는 `@lru_cache`로 인해 재시작 전까지 이전 값을 계속 사용한다.
   "즉시 차단"은 재시작을 포함한 절차이며 코드 롤백이나 재배포가 필요하다는 뜻은 아니다.
4. 대상을 팀/테스트 계정으로 좁히는 확대 단계는 이 게이트가 아니라 기존 `NOTIFICATION` 동의를
   재사용한다. 이 이슈는 별도 allowlist를 만들지 않으며, 대상 제한의 실효성은 이미 동의를
   켠 사용자가 없는지 확인하는 절차에 의존한다.

## 범위와 승인 경계

이 결정은 게이트 플래그 자체의 존재와 계약 정합만 다룬다. 팀→베타→전체 단계적 확대 실행,
VAPID/암호화 키 발급, #471 실기기 검증은 이 결정의 승인 대상이 아니며 각 단계마다 권가빈
승인이 필요하다.
