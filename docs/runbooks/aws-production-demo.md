# AWS Production 합성 데이터 데모 Runbook

## 목적과 사용 범위

이 Runbook은 발표와 최대 1주일의 제한된 데모를 위해 단일 AWS EC2에서
Frontend, Nginx, FastAPI, PostgreSQL, Redis를 Docker Compose로 실행하는 절차입니다.
실제 환자 정보·처방전·진료기록은 입력하지 않고 승인된 비식별 합성 데이터만 사용합니다.

이 구성은 일반 사용자 대상 의료 서비스 공개 승인을 뜻하지 않습니다.
`PUBLIC_TRACK_C`와 `PUBLIC_TRACK_F` 공개 게이트는 외부 승인·Privacy·의료 안전 조건이
충족되기 전까지 닫아 둡니다. 현재 구현된 runtime 설정인 `PUBLIC_TRACK_F_ENABLED`도
`false`로 유지합니다. 실제 Redis Consumer가 연결되지 않은 `ai-worker`는 데모 배포
대상에 포함하지 않습니다.

기술 배포 승인, 기술 Rollback 판단과 대체 담당자는
[Issue #230](https://github.com/AI-HealthCare-05/AH_05_04/issues/230)에서 별도로
확정합니다. 이 Runbook이 미정인 승인 권한을 대신하지 않습니다.

## 권장 AWS 구성

- Ubuntu LTS EC2 `t3.medium` 1대와 `gp3` 30 GiB
- 고정 접속 주소를 위한 Elastic IP 1개
- EC2를 가리키는 데모 전용 DNS A record 1개
- Security Group inbound
  - SSH `22`: 배포 실행자의 고정 IP만 허용
  - HTTP `80`: `0.0.0.0/0`, 인증서 HTTP-01 발급과 HTTPS redirect용
  - HTTPS `443`: `0.0.0.0/0`, 데모 접속용
- EC2 outbound: Docker registry, Let’s Encrypt, 승인된 Provider endpoint에 필요한 HTTPS

데모 중 실제 부하를 확인해 CPU, memory 또는 disk가 부족하면 인스턴스를 확장합니다.
단일 EC2이므로 인스턴스 장애에 대한 고가용성은 제공하지 않습니다.

## 1. 배포 전 준비

1. 배포할 commit을 확정하고 짧은 commit SHA를 `APP_VERSION`과
   `FRONTEND_VERSION`에 사용합니다. `latest`는 사용하지 않습니다.
2. Rollback 후보인 직전 정상 `APP_VERSION`과 `FRONTEND_VERSION`을 별도로 기록합니다.
3. EC2에 Docker Engine과 Compose plugin을 설치하고 Docker registry 로그인이 가능한지
   확인합니다.
4. DNS A record가 Elastic IP를 가리키는지 확인합니다.
5. `envs/example.prod.env`를 `envs/.prod.env`로 복사하고 모든 placeholder를 실제 값으로
   바꿉니다. 이 파일은 커밋하지 않습니다.
6. 아래 값은 반드시 동일 origin을 사용합니다.

```dotenv
PRODUCTION_DOMAIN=demo.example.com
PRODUCTION_PUBLIC_ORIGIN=https://demo.example.com
COOKIE_DOMAIN=demo.example.com
CORS_ALLOWED_ORIGINS=https://demo.example.com
```

`CERTBOT_EMAIL`에는 인증서 만료 안내를 받을 주소를 사용합니다. Secret, Provider key,
DB·Redis 비밀번호는 터미널 출력이나 PR 증빙에 남기지 않습니다.

## 2. 최초 HTTP 배포

저장소 루트에서 다음 명령을 실행합니다.

```bash
bash scripts/deployment.sh
```

- Docker registry PAT, `~/.ssh` 아래의 EC2 key 파일명, EC2 IP 또는 hostname을 입력합니다.
- 최초 배포의 HTTP/HTTPS 선택에서는 `1) HTTP`를 선택합니다.
- 스크립트는 FastAPI와 Frontend 이미지를 고정 태그로 build·push하고, migration 전
  backup과 snapshot을 만든 뒤 FastAPI와 Nginx를 배포합니다.
- migration 또는 profile 무결성 검증이 실패하면 신규 애플리케이션을 시작하지 않습니다.

EC2에서 다음 상태를 확인합니다.

```bash
cd ~/project
docker compose ps
docker compose logs --no-color --tail=100 fastapi nginx
```

`postgres`, `redis`, `fastapi`, `nginx`가 healthy이고 `migrate`가 정상 종료되어야 합니다.

## 3. HTTPS 인증서 발급과 적용

DNS 전파가 완료된 뒤 저장소 루트에서 실행합니다.

```bash
bash scripts/certbot.sh
```

스크립트는 HTTP challenge 설정을 적용하고 `envs/.prod.env`의 도메인과 이메일로
Let’s Encrypt 인증서를 발급합니다. 이어서 Nginx HTTPS 설정을 `nginx -t`로 검증한 뒤
reload하고 갱신용 Certbot 서비스를 시작합니다. 저장소 원본 Nginx 파일은 수정하지
않습니다.

인증서 발급이 실패하면 DNS A record, Security Group의 80 port, 도메인의 기존 AAAA
record와 Nginx 로그를 확인합니다. HTTPS 설정을 먼저 적용하지 말고 HTTP challenge
상태에서 원인을 해결합니다.

## 4. 배포 Smoke test

실제 도메인으로 아래를 확인합니다.

```bash
curl --fail --show-error --silent https://demo.example.com/healthz
curl --fail --show-error --silent https://demo.example.com/api/v1/health
curl --head --fail --show-error https://demo.example.com/
```

브라우저 smoke는 동일한 합성 계정으로 아래 순서를 중간 생략 없이 수행합니다. 시작 전
승인된 합성 처방전 fixture와 계정의 비밀값이 아닌 식별 label을 기록합니다.

1. 루트 URL에서 Frontend가 열리고 새로고침해도 SPA route가 404가 되지 않는지 확인합니다.
2. 합성 계정으로 로그인하고 합성 처방전을 업로드한 뒤 OCR 결과를 검수·확정합니다.
   기존에 확정한 합성 처방을 재사용할 때도 해당 처방의 약 목록이 화면에서 정상적으로
   조회되는지 먼저 확인합니다.
3. 확정 완료 화면에서 복약 가이드로 진입해 `COMPLETED` 가이드의 약 목록과 복약 안내가
   해당 합성 처방과 일치하는지 확인합니다.
4. 가이드의 `복약 챗봇 도지와 이야기하기` 버튼으로 Chat에 진입합니다. 합성 질문 1건을
   전송해 USER 질문과 `COMPLETED` ASSISTANT 답변이 모두 보이는지 확인합니다.
5. 메뉴에서 로그아웃하고 보호된 처방·가이드·대화가 더 이상 보이지 않으며 시작 화면으로
   이동하는지 확인합니다.
6. 같은 합성 계정으로 다시 로그인합니다. 새 처방전 업로드나 Guide·Chat 생성 요청 없이
   가이드 탭으로 진입해 기존 최신 Prescription과 연결된 Guide가 다시 표시되는지 확인합니다.
7. 복원된 Guide에서 다시 Chat으로 진입해 로그아웃 전에 만든 동일 세션의 USER 질문과
   ASSISTANT 답변이 빈 대화로 바뀌지 않고 복원되는지 확인합니다.

개발자 도구 Network에서는 Frontend 요청이 동일 origin의 `/api/v1/*`로 전달되고, 재로그인
뒤 `GET /api/v1/prescriptions/latest`, `GET /api/v1/prescriptions/<redacted>/guide`,
`GET /api/v1/prescriptions/<redacted>/chat-session`,
`GET /api/v1/chat-sessions/<redacted>/messages`가 성공하는지 확인합니다. 재발견 단계에서
Guide 또는 Chat을 새로 만드는 `POST`가 발생하면 통과로 기록하지 않습니다.

각 단계의 PASS/FAIL, 실행 시각, 브라우저 버전, 비밀값이 아닌 합성 fixture label, 재로그인
전·후의 가이드 및 대화 표시 결과, 위 API의 method·redacted path·status를
`deployment-evidence/<timestamp>/`에 기록합니다. 배포 commit, 이미지 태그·digest, 승인자와
증적 위치를 배포 PR에 남깁니다. 요청·응답 body, Authorization/Cookie header, Secret,
계정 비밀번호, 원본 의료 데이터는 캡처하거나 첨부하지 않습니다.

실제 환자 데이터는 입력하지 않습니다. 공개 게이트가 닫힌 기능은 데모 성공으로 간주하거나
임의로 활성화하지 않습니다. 이 실제 AWS smoke는 merge 후 Issue #338에서 수행하며, 실행 전
Runbook에 절차가 있다는 사실만으로 smoke를 통과한 것으로 간주하지 않습니다.

## 5. 이후 재배포

새 commit의 고정 `APP_VERSION`과 `FRONTEND_VERSION`으로 `envs/.prod.env`를 갱신한 뒤
`scripts/deployment.sh`를 다시 실행하고 `2) HTTPS`를 선택합니다. 인증서가 이미 발급된
서버에서만 HTTPS를 선택합니다. 완료 후 Smoke test를 반복합니다.

## 6. 애플리케이션 Rollback

Rollback 판단과 실행 승인을 먼저 기록합니다. DB schema는 자동 downgrade하지 않습니다.
새 migration이 이전 이미지와 호환되지 않으면 이전 FastAPI 이미지를 강제로 올리지 말고
후속 migration으로 forward-fix합니다.

호환성이 확인된 애플리케이션 이미지 rollback은 EC2에서 수행합니다.

```bash
cd ~/project
cp .env ".env.before-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
vi .env
docker compose pull fastapi nginx
docker compose up -d --no-deps --wait fastapi
docker compose up -d --no-deps --wait nginx
docker compose ps
```

`vi .env`에서 `APP_VERSION`과 `FRONTEND_VERSION`만 기록해 둔 직전 정상 태그로
변경합니다. 복구 후 `/healthz`, `/api/v1/health`, Frontend 주요 합성 데이터 흐름을 다시
확인하고 사용한 이미지 digest와 결과를 기록합니다.

## 7. 데모 종료와 비용 정리

1. 필요한 비식별 배포 증빙과 승인된 DB backup의 보관 위치를 확인합니다.
2. DNS에서 데모 record를 제거하거나 비공개 상태로 전환합니다.
3. Security Group의 80·443 inbound를 제거합니다.
4. EC2에서 `docker compose down`으로 서비스를 중지합니다. 이 명령은 named volume을
   자동 삭제하지 않습니다.
5. 보존·파기 정책과 승인에 따라 EC2, EBS volume, snapshot을 종료 또는 삭제합니다.
6. 사용하지 않는 Elastic IP를 release해 추가 비용을 막습니다.
7. Docker registry의 데모 이미지 보존 여부와 Secret·PAT rotation 필요 여부를 확인합니다.

`docker compose down -v`, EBS 삭제, EC2 termination은 데이터를 복구하기 어렵게 만들 수
있으므로 대상과 backup을 확인하고 승인된 실행자가 별도로 수행합니다.
