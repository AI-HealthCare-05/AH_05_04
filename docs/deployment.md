# 배포 가이드

## 환경

- Local
- Staging
- Production

## 배포 절차

인프라가 확정되면 이미지 빌드, 환경변수 설정, DB 마이그레이션, 배포 및 롤백 절차를 기록합니다.

### 복약 챗봇 동기 요청 사전 조건

현재 기본값 `OPENAI_TIMEOUT_SECONDS=20`에서 같은 채팅 세션의 요청은 외부 생성이 끝날 때까지 해당 세션 row lock을 유지한다. 배포 대상은 다음 조건을 모두 만족해야 한다.

- Nginx `proxy_read_timeout >= 45s`
- MySQL/InnoDB `innodb_lock_wait_timeout > 20s`

두 값 중 하나라도 이 기준보다 낮으면 Infrastructure가 설정을 조정한 뒤에 배포한다. 이 불일치를 감추기 위해 애플리케이션의 OpenAI timeout을 줄이지 않는다.

배포 전 아래 기록을 대상 환경별로 완료한다.

| Environment | Observed `proxy_read_timeout` | Observed `innodb_lock_wait_timeout` | Verification date | Verifier | Result / follow-up |
| --- | --- | --- | --- | --- | --- |
| Local |  |  |  |  |  |
| Staging |  |  |  |  |  |
| Production |  |  |  |  |  |

`RUN_OPENAI_CHAT_SMOKE=1`은 비식별 합성 입력과 비밀 API key가 필요한 선택적 smoke test이며, 일반 PR gate나 배포 사전 조건으로 실행하지 않는다.

## 보안 확인

- 비밀정보는 저장소에 커밋하지 않습니다.
- 운영 환경변수와 인증서는 승인된 비밀 저장소에서 관리합니다.
- 로그와 오류 응답에 환자 개인정보가 노출되지 않는지 확인합니다.
- Provider 요청·응답 본문, 질문·약물 정보, 원본 예외 chain은 로그·오류 응답·배포 기록에 남기지 않습니다.
