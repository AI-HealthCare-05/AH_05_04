# 배포 가이드

## 환경

- Local
- Staging
- Production

## 배포 절차

인프라가 확정되면 이미지 빌드, 환경변수 설정, DB 마이그레이션, 배포 및 롤백 절차를 기록합니다.

## 복약 챗봇 배포 기록

복약 챗봇은 AI 생성 중에 DB transaction과 connection을 유지하고, 같은 세션의 요청은 row lock에서 직렬화합니다. 배포마다 아래 값과 승인 결과를 실제 운영 설정 기준으로 기록합니다.

이 기록은 Issue #38 구현·merge 완료와 분리된 production deployment gate입니다. 대상 환경이 정해지기 전에 값을 추정하거나 승인자를 대신 기입하지 않습니다. 아래 표가 비어 있거나 조건을 충족하지 않으면 구현 검증 통과 여부와 관계없이 해당 환경에 배포할 수 없습니다.

| 항목 | 배포 기록 |
| --- | --- |
| 환경·배포 식별자 |  |
| 확인일·확인자 |  |
| OpenAI 전체 timeout `T` | 실제 `OPENAI_TIMEOUT_SECONDS`: ____초 (코드 기본값: 20초) |
| 애플리케이션 처리 여유 `M` | ____초 (기본 참고값: 5초) |
| Nginx read timeout | 실제 `proxy_read_timeout`: ____초 / 필요 하한 `2 × T + M`: ____초 / 충족 여부: ____ |
| MySQL lock wait timeout | 실제 `innodb_lock_wait_timeout`: ____초 / 계산된 `T + M`: ____초 / 초과 여부: ____ |
| worker 수 |  |
| worker별 전체 in-flight chat | AI 호출 중인 요청과 lock waiter를 모두 포함: ____ |
| worker별 비채팅 예비 connection | 인증·처방 조회 등을 위해 ____개 예약 |
| worker별 DB pool | 실제 pool size: ____ / overflow: ____ / 총 수용량: ____ |
| DB connection wait 정책 | pool wait timeout·queue 정책: ____ / 허용 가능한 대기: ____ |
| 외부 생성 중 DB connection 점유 | tradeoff 승인 여부·승인자: ____ |
| 수용량 판정 | `전체 in-flight chat <= pool + overflow - 비채팅 예비 connection`: ____ |

배포 기본 참고값 `T=20초`, `M=5초`를 사용하면 Nginx read timeout은 `2 × 20 + 5 = 45초` 이상이어야 하고 MySQL lock wait timeout은 `20 + 5 = 25초`를 초과해야 합니다. `proxy_read_timeout`은 전체 요청 상한이 아니라 두 연속 read 사이의 무응답 상한이지만, 현재 non-streaming 응답은 생성 완료 전에 body를 보내지 않으므로 이 기준을 확인합니다.

다음 조건을 모두 충족해야 배포할 수 있습니다.

- 실제 `proxy_read_timeout >= 2 × T + M`을 확인합니다.
- 실제 `innodb_lock_wait_timeout > T + M`을 확인합니다.
- worker별 전체 in-flight chat에 row lock waiter를 포함하고, pool·overflow 총 수용량에서 비채팅 예비 connection을 먼저 제외해 수용 가능한지 확인합니다.
- DB pool size, overflow, pool wait timeout·queue 정책과 허용 가능한 connection 대기시간을 실제 배포 설정으로 기록합니다. 저장소는 `DB_CONNECTION_POOL_MAXSIZE`만 명시적으로 설정하므로 overflow와 wait 정책은 배포 런타임의 실제 값을 확인합니다.
- AI 외부 호출 동안 DB transaction과 connection을 유지하는 현재 설계를 해당 수용량에서 운영할 것인지 명시적으로 승인합니다.

수용량이 부족하거나 비채팅 요청의 connection 대기가 허용 범위를 넘으면 배포하지 않습니다. 세션 잠금을 조용히 약화하는 대신 admission/rate limiting 또는 비동기 worker 설계를 먼저 도입합니다.

## 보안 확인

- 비밀정보는 저장소에 커밋하지 않습니다.
- 운영 환경변수와 인증서는 승인된 비밀 저장소에서 관리합니다.
- 로그와 오류 응답에 환자 개인정보가 노출되지 않는지 확인합니다.
