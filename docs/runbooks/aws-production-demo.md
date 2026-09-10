# AWS Production 합성 데이터 데모 Runbook

## 목적과 사용 범위

이 Runbook은 2026-09-22부터 2026-09-30까지 9일 동안 단일 AWS EC2와 CloudFront
기본 주소로 Frontend, Nginx, FastAPI, PostgreSQL, Redis를 실행하는 절차입니다.
별도 도메인을 구매하지 않고 CloudFront가 발급한 `https://<distribution>.cloudfront.net`
주소와 기본 인증서를 사용합니다.

실제 환자 정보·처방전·진료기록은 입력하지 않고 승인된 비식별 합성 데이터만
사용합니다. 이 구성은 일반 사용자 대상 의료 서비스 공개 승인이 아닙니다.
`PUBLIC_TRACK_C`와 `PUBLIC_TRACK_F` 공개 게이트는 닫아 두고, 현재 runtime 설정인
`PUBLIC_TRACK_F_ENABLED`도 `false`로 유지합니다.

현재 MVP의 OCR 요청은 Outbox·Redis Stream·`ai-worker`를 사용하는 비동기 경로이며,
Consumer, 실제 CLOVA OCR Provider와 Outbox Publisher 주기 실행까지 구현되어 있습니다.
다만 이 Runbook이 사용하는 `scripts/deployment.sh`는 Worker health check·운영 관제와
Production 배포 조립을 아직 포함하지 않아 `ai-worker` image를 build·push·시작하지
않습니다. 따라서 이 제한된 AWS 데모 구성만으로는 OCR Job을 terminal 상태까지 처리할
수 없으며, 아래 전체 MVP browser smoke를 통과했다고 기록할 수 없습니다. Worker를
Production 배포 대상에 추가하는 작업은 해당 운영 조건과 검증을 함께 완료하는 별도
구현 범위입니다.

## 책임과 배포 차단

기간 한정 데모의 예정 역할은 다음과 같습니다.

- 제품·Release Gate 확인, 배포·Rollback 실행, 관제 총괄: 권가빈
- 기술 배포 승인과 기술 Rollback 판단: 정현우
- Backend·API·DB·Security 운영 검증: 송은영
- Worker·Redis·OCR 운영 검증: 김지혜
- Frontend smoke/E2E 운영 검증: 남한솔
- 대체 배포·Rollback 실행자: 미정

기술 승인자 정현우가 비상시에 실행까지 맡을 때는 해당 배포를 승인할 별도 기술
승인자를 먼저 지정합니다. 담당자의 수락, AWS 최소권한과 Runbook 수행 가능성은
[Issue #230](https://github.com/AI-HealthCare-05/AH_05_04/issues/230)에서 확인합니다.
미정 대체 실행자와 필수 접근권한이 확정되지 않으면 배포하지 않습니다.

## 권장 AWS 구성

- Region: `ap-northeast-2`
- Ubuntu LTS x86_64 EC2 `t3.medium` 1대와 암호화된 `gp3` 30 GiB
- CloudFront distribution 1개와 AWS가 발급한 기본 `*.cloudfront.net` hostname
- EC2 Security Group inbound
  - SSH `22`: 권가빈과 대체 실행자의 승인된 고정 IP만 허용
  - HTTP `80`: AWS managed prefix list
    `com.amazonaws.global.cloudfront.origin-facing`만 허용
  - HTTPS `443`: 열지 않음. Viewer HTTPS는 CloudFront에서 종료
- PostgreSQL `5432`, Redis `6379`, FastAPI `8000`, Vite `5173`: host 미공개
- EC2 outbound: Docker registry와 승인된 Provider endpoint에 필요한 HTTPS

CloudFront origin custom header와 Security Group prefix list를 함께 사용해 사용자가 EC2
origin을 직접 우회하지 못하게 합니다. 단일 EC2이므로 고가용성은 제공하지 않습니다.

## 1. 계정과 비용 안전장치

이 데모는 AWS 유료 플랜을 사용하며 Free Tier credit이나 무료 사용 한도를 배포 조건으로
삼지 않습니다.

1. AWS root는 MFA를 활성화하고 공유하거나 root access key를 만들지 않습니다.
2. 권가빈과 대체 실행자는 서로 다른 IAM Identity Center 사용자와 SSH key를 사용합니다.
3. 기술 승인자에게 실행 권한이 필요하지 않으면 ReadOnly 범위만 부여합니다.
4. 팀이 승인한 비용 상한으로 AWS Budget을 만들고 50%, 80%, 100% actual-cost 알림을
   권가빈에게 설정합니다.
5. 2026-09-30 철거 일정을 팀 캘린더에 등록합니다.
6. 배포할 commit SHA와 직전 정상 `APP_VERSION`, `FRONTEND_VERSION`을 기록합니다.

CloudFront 기본 hostname과 기본 인증서를 쓰면 별도 도메인을 구매하지 않아도 됩니다.
이는 무료 배포라는 뜻이 아니며 EC2, EBS, CloudFront 전송·요청 등 실제 AWS 사용량은
유료로 청구됩니다.

## 2. EC2와 CloudFront 준비

1. 위 사양의 EC2를 생성하고 Docker Engine과 Compose plugin을 설치합니다.
2. EC2의 public IPv4 DNS hostname을 CloudFront custom origin으로 등록합니다.
3. CloudFront origin protocol은 `HTTP only`, HTTP port는 `80`으로 설정합니다.
4. Origin response timeout을 `75초`로 설정합니다. 기본 30초에 의존하면 OCR 전체
   deadline 60초의 응답 여유가 없습니다.
5. Origin custom header를 추가합니다.
   - Name: `X-Origin-Verify`
   - Value: password manager로 생성한 32~128자 무작위 영문자·숫자·`_`·`-` 값
6. Default cache behavior를 다음과 같이 설정합니다.
   - Viewer protocol policy: `Redirect HTTP to HTTPS`
   - Allowed HTTP methods: `GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE`
   - Cache policy: managed `CachingDisabled`
   - Origin request policy: managed `AllViewerExceptHostHeader`
7. Distribution 배포 후 발급된 `<distribution>.cloudfront.net` hostname을 기록합니다.
8. EC2 Security Group의 80번을 CloudFront origin-facing managed prefix list로만 제한합니다.

CachingDisabled와 전체 viewer header·cookie·query 전달을 사용해 인증된 API 응답이 다른
사용자에게 cache되지 않게 하고 `Authorization`과 cookie가 Backend까지 전달되게 합니다.
Origin header 값은 자격 증명처럼 취급하며 Issue, PR, 로그에 기록하지 않습니다.

## 3. Production 환경파일 준비

`envs/example.prod.env`를 `envs/.prod.env`로 복사하고 모든 placeholder를 실제 값으로
바꿉니다. 파일 권한은 `600`으로 유지하고 커밋하지 않습니다.

CloudFront 관련 값은 다음처럼 동일 origin으로 맞춥니다.

```dotenv
TLS_TERMINATION=cloudfront
PRODUCTION_DOMAIN=d111111abcdef8.cloudfront.net
PRODUCTION_PUBLIC_ORIGIN=https://d111111abcdef8.cloudfront.net
COOKIE_DOMAIN=d111111abcdef8.cloudfront.net
CORS_ALLOWED_ORIGINS=https://d111111abcdef8.cloudfront.net
CLOUDFRONT_ORIGIN_VERIFY_SECRET=<CloudFront-X-Origin-Verify와-같은-무작위-값>
CERTBOT_EMAIL=
```

FastAPI idempotency snapshot 암호화 key ring도 함께 준비합니다.

```dotenv
IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY=<Fernet.generate_key로-생성한-실제-키>
IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY_VERSION=v1
IDEMPOTENCY_SNAPSHOT_ENCRYPTION_RETIRED_KEYS={}
```

active key는 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`로
생성하고 `envs/example.prod.env`의 공개 placeholder를 그대로 사용하지 않습니다. 최초 배포는
version `v1`, retired keys `{}`로 시작합니다. 키 교체 시에는 version을 올리고 TTL이 남은
snapshot의 이전 key를 원래 version과 함께 retired keys에 보관합니다. 실제 key는 공유
터미널 기록이나 PR 증빙에 남기지 않습니다.

`CLOUDFRONT_ORIGIN_VERIFY_SECRET`, snapshot 암호화 key, API Key, DB·Redis 비밀번호,
Docker PAT과 계정 비밀번호를 터미널 출력이나 PR 증빙에 남기지 않습니다. CloudFront custom
header와 env의 origin secret이 다르면 모든 Frontend/API origin 요청이 `403`으로 실패합니다.

## 4. 배포

저장소 루트에서 다음 명령을 실행합니다.

```bash
bash scripts/deployment.sh
```

- Docker registry PAT, `~/.ssh` 아래 EC2 key 파일명, EC2 IP 또는 hostname을 입력합니다.
- `TLS_TERMINATION=cloudfront`이면 HTTP/HTTPS 선택을 묻지 않고 CloudFront origin용 HTTP
  Nginx 설정을 적용합니다.
- FastAPI와 Frontend image를 고정 태그로 build·push합니다.
- PostgreSQL과 Redis health를 기다린 뒤 migration 전 backup·snapshot을 생성합니다.
- 기존 애플리케이션 중지, migration과 profile 무결성 검증이 성공한 경우에만 FastAPI와
  Nginx를 시작합니다.
- CloudFront 모드에서는 `scripts/certbot.sh`를 실행하지 않습니다.

EC2에서 다음 상태를 확인합니다.

```bash
cd ~/project
docker compose ps
docker compose logs --no-color --tail=100 fastapi nginx
```

`postgres`, `redis`, `fastapi`, `nginx`가 healthy이고 `migrate`가 정상 종료되어야 합니다.

## 5. 배포 Smoke test

CloudFront 기본 주소로만 확인합니다.

```bash
curl --fail --show-error --silent https://d111111abcdef8.cloudfront.net/healthz
curl --fail --show-error --silent https://d111111abcdef8.cloudfront.net/api/v1/health
curl --head --fail --show-error https://d111111abcdef8.cloudfront.net/
```

EC2 public DNS의 `/`, `/assets/*`, `/api/*`에 `X-Origin-Verify` 없이 직접 접근했을 때
`403`이고, 외부에서 443·5432·6379·8000·5173에 연결할 수 없는지 확인합니다. `/healthz`는
컨테이너 health check를 위해 header를 요구하지 않지만 Security Group이 CloudFront 외
접근을 차단해야 합니다.

위 검사는 현재 Worker 미포함 배포 범위의 인프라·Frontend·Backend 기본 smoke입니다.
로그인과 보호 route 접근까지 확인할 수 있지만, 비동기 OCR 완료를 전제로 하는 전체 MVP
흐름의 통과 증빙은 아닙니다.

### 전체 MVP browser smoke — Worker Production 조립 전 실행 차단

아래 절차는 Worker를 Production 배포 대상에 포함하고 health check·관제·Provider
secret·공유 storage 검증까지 완료한 뒤에만 동일한 합성 계정으로 중간 생략 없이
수행합니다. 현재 `scripts/deployment.sh`의 배포 결과에서는 2단계 OCR Job이 terminal
상태에 도달하지 않으므로 이후 단계를 실행하거나 PASS로 기록하지 않습니다.

1. 루트 URL과 새로고침에서 SPA route가 404가 되지 않는지 확인합니다.
2. 합성 계정으로 로그인하고 합성 처방전을 업로드한 뒤 OCR 결과를 검수·확정합니다.
3. 확정 Prescription에서 복약 가이드로 진입해 약 목록과 안내를 확인합니다.
4. Guide의 `복약 챗봇 도지와 이야기하기` 버튼으로 Chat에 진입해 합성 질문 1건과
   완료 답변을 확인합니다.
5. 로그아웃하고 보호된 데이터가 보이지 않는지 확인합니다.
6. 같은 합성 계정으로 다시 로그인해 새 생성 요청 없이 기존 최신 Prescription과 연결된 Guide가
   복원되는지 확인합니다.
7. 복원된 Guide에서 기존 Chat의 동일 세션의 USER 질문과 ASSISTANT 답변이 복원되는지
   확인합니다.

Network에서는 동일 origin의 다음 요청이 성공하는지 확인합니다.

- `GET /api/v1/prescriptions/latest`
- `GET /api/v1/prescriptions/<redacted>/guide`
- `GET /api/v1/prescriptions/<redacted>/chat-session`
- `GET /api/v1/chat-sessions/<redacted>/messages`

재발견 단계에서 Guide 또는 Chat을 새로 만드는 `POST`가 발생하면 통과로 기록하지 않습니다.
단계별 PASS/FAIL, 실행 시각, 브라우저 버전, 합성 fixture label, redacted
path·status, 배포 commit과 image digest를 `deployment-evidence/<timestamp>/`와 배포 PR에
기록합니다. 요청·응답 body, Authorization/Cookie header, Secret, 비밀번호와 원본 의료
데이터는 캡처하지 않습니다.

기본 배포 smoke는 배포 후 Issue #338에서 수행합니다. 전체 MVP browser smoke는 Worker
Production 조립을 완료한 후 별도 배포 Issue 또는 PR에서 수행합니다.
Runbook에 절차가 있다는 사실만으로 smoke를 통과한 것으로 간주하지 않습니다. Worker의
Local·통합 테스트 통과도 AWS Production smoke를 대신하지 않습니다.

## 6. 이후 재배포

새 commit의 고정 `APP_VERSION`과 `FRONTEND_VERSION`으로 `.prod.env`를 갱신하고
`scripts/deployment.sh`를 다시 실행합니다. CloudFront distribution과 origin secret은
그대로 유지합니다. 완료 후 전체 Smoke test를 반복합니다.

## 7. 애플리케이션 Rollback

정현우의 기술 Rollback 판단과 권가빈의 실행 기록을 먼저 남깁니다. DB schema는 자동
downgrade하지 않습니다. 새 migration이 이전 image와 호환되지 않으면 이전 FastAPI를
강제로 올리지 말고 후속 migration으로 forward-fix합니다.

호환성이 확인된 image rollback은 EC2에서 수행합니다.

```bash
cd ~/project
cp .env ".env.before-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
vi .env
docker compose pull fastapi nginx
docker compose up -d --no-deps --wait fastapi
docker compose up -d --no-deps --wait nginx
docker compose ps
```

`vi .env`에서 `APP_VERSION`과 `FRONTEND_VERSION`만 직전 정상 태그로 변경합니다. 복구 후
CloudFront 주소의 health, Frontend와 합성 데이터 흐름을 다시 확인하고 image digest와
결과를 기록합니다.

## 8. 2026-09-30 철거

1. 필요한 비식별 증빙과 승인된 DB backup의 보관 위치를 확인합니다.
2. CloudFront distribution을 Disable하고 배포가 끝난 뒤 Delete합니다.
3. Security Group의 80번과 22번 inbound를 제거합니다.
4. EC2에서 `docker compose down`으로 서비스를 중지합니다. named volume은 유지됩니다.
5. 보존·파기 정책과 승인에 따라 EC2와 EBS volume·snapshot을 종료 또는 삭제합니다.
6. 사용하지 않는 Elastic IP가 있으면 release합니다.
7. IAM Identity Center 배포 권한과 SSH public key를 제거합니다.
8. Docker registry PAT과 CloudFront origin secret을 폐기·회전합니다.
9. 비용 탐색기에서 남은 EC2, EBS, Elastic IP, CloudFront 리소스가 없는지 다음 날 다시
   확인합니다.

`docker compose down -v`, EBS 삭제, EC2 termination은 복구하기 어려운 작업입니다.
대상, backup과 승인 기록을 확인한 뒤 권가빈이 실행하고 정현우가 기술 판단 기록을
남깁니다.

## AWS 참고 자료

- [CloudFront distribution 설정](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesGeneral.html)
- [CloudFront managed cache policies](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html)
- [CloudFront managed origin request policies](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html)
- [Origin custom header](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/add-origin-custom-headers.html)
- [CloudFront origin-facing managed prefix list](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/LocationsOfEdgeServers.html)
- [Origin connection과 response timeout](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesOrigin.html)
