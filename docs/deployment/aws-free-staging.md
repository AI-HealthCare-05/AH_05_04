# AWS 무료 EC2 Staging Runbook

## 목적과 제한

이 Runbook은 2026-09-14 Production 배포 전에 AWS Free Plan의 무료 크레딧
범위에서 단일 EC2 배포 흐름을 연습하기 위한 내부 Staging 절차입니다. 실제
환자·처방·진료 데이터는 사용하지 않고 승인된 비식별 합성 데이터만 사용합니다.

이 환경은 Production이 아닙니다. Production 공개 승인, 외부 Provider 전송 승인,
공통 Privacy Production gate, `PUBLIC_TRACK_C`와 `PUBLIC_TRACK_F`를 해제하거나
대체하지 않습니다. HTTP만 사용하는 첫 리허설에서는 `Secure` refresh cookie를
검증할 수 없으므로 인프라·migration·단기 smoke까지만 수행합니다. 로그인 유지와
token refresh를 포함한 브라우저 E2E는 도메인과 HTTPS를 적용한 뒤 검증합니다.

## 구성

```text
Internet :80
    |
    v
Frontend static files + Nginx
    |
    +-- /api/* --> FastAPI:8000
                       |
                       +-- PostgreSQL:5432
                       +-- approved external providers

Redis:6379 (Docker internal only, current synchronous MVP path에서는 미사용)
```

- 하나의 x86_64 Ubuntu EC2에서 Docker Compose를 실행합니다.
- PostgreSQL, Redis, FastAPI는 host port를 publish하지 않습니다.
- Nginx만 host port 80을 publish합니다.
- Frontend는 Vite 개발 서버가 아니라 production build 결과물을 Nginx가 제공합니다.
- FastAPI는 무료 EC2 메모리를 고려해 Uvicorn worker 1개로 실행합니다.
- 실제 Redis Consumer 경로가 연결되지 않은 `ai-worker`는 실행하지 않습니다.
- PostgreSQL과 합성 업로드 파일은 이름이 고정된 Docker volume에 저장합니다.

## 1. AWS 비용 보호

AWS Console의 `Billing and Cost Management`에서 계정의 Free Plan 종료일과 credit
잔액을 확인합니다. `Budgets`에서 `Zero spend budget`을 만들고 Free Tier usage
알림을 활성화합니다. Free Tier 대상과 잔액은 계정마다 Console에 표시되는 값을
정본으로 사용합니다.

- [AWS Free Tier](https://aws.amazon.com/free/)
- [EC2 Free Tier 사용량 확인](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html)
- [Zero spend budget](https://docs.aws.amazon.com/cost-management/latest/userguide/budget-templates.html)

## 2. EC2 생성

AWS Console에서 아래 기준으로 인스턴스를 만듭니다.

| 항목 | Staging 값 |
| --- | --- |
| 이름 | `finalproject-staging` |
| Region | `ap-northeast-2`(Seoul) |
| AMI | Free Tier eligible Ubuntu Server 24.04 LTS, x86_64 |
| Instance type | Console에서 Free Tier eligible로 표시되는 `t3.small` 권장 |
| Storage | `gp3` 20GiB |
| Public IPv4 | 1개 |

현재 build script는 `linux/amd64` 이미지를 만들므로 ARM 기반 `t4g`를 선택하지
않습니다. `t3.micro`의 1GiB 메모리는 전체 Compose를 실행하기에 부족할 수 있으므로
Console에서 자격이 확인되는 신규 Free Plan 계정은 `t3.small`을 우선합니다.

Security Group inbound는 아래처럼 제한합니다.

| Port | Source | 목적 |
| --- | --- | --- |
| 22 | 배포 실행자의 현재 public IP `/32` | SSH |
| 80 | 내부 검증 참여자의 승인된 IP 대역 | 초기 HTTP smoke |
| 443 | 내부 검증 참여자의 승인된 IP 대역 | HTTPS 적용 후 smoke |

`5432`, `6379`, `8000`, `5173` inbound rule을 만들지 않습니다. 내부 검증자의 IP를
제한할 수 없다는 이유로 실제 데이터 사용이나 Production 공개를 허용하지 않습니다.

## 3. EC2 Docker 준비

발급한 PEM 파일의 권한을 제한하고 접속합니다.

```bash
chmod 400 /absolute/path/to/finalproject-staging.pem
ssh -i /absolute/path/to/finalproject-staging.pem ubuntu@EC2_PUBLIC_IP
```

Ubuntu에 Docker Engine과 Docker Compose plugin을 설치한 뒤 `ubuntu` 사용자가
Docker를 실행할 수 있게 설정합니다. 설치는 Docker 공식 Ubuntu 절차를 사용합니다.

- [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)

재접속 후 아래 두 명령이 성공해야 합니다.

```bash
docker --version
docker compose version
```

## 4. Docker Hub 준비

Backend와 Frontend 이미지를 같은 Docker Hub repository에 다음 immutable tag로
push합니다.

```text
<user>/<repository>:app-<commit-or-version>
<user>/<repository>:frontend-<commit-or-version>
```

`latest`는 rollback 대상을 식별할 수 없으므로 배포 스크립트가 거부합니다. Docker
Hub 비밀번호 대신 최소 권한 Personal Access Token을 사용합니다. PAT은 환경파일이나
Issue·PR·배포 증빙에 기록하지 않습니다.

## 5. Staging 환경파일

저장소 루트에서 예시를 복사합니다.

```bash
cp envs/example.staging.env envs/.staging.env
chmod 600 envs/.staging.env
```

고정 image version에는 배포할 commit SHA를 사용할 수 있습니다.

```bash
git rev-parse --short=12 HEAD
```

서로 다른 secret은 hex로 생성합니다.

```bash
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 24
openssl rand -hex 24
openssl rand -hex 24
```

첫 두 값은 `SECRET_KEY`, `IDEMPOTENCY_HMAC_KEY`에 사용합니다. 나머지는 서로 다른
`DB_ADMIN_PASSWORD`, `DB_MIGRATION_PASSWORD`, `DB_APP_PASSWORD`에 사용합니다.
값을 명령 이력, 채팅, 문서나 Git에 복사하지 않습니다.

HTTP 첫 리허설 예시는 다음 관계를 만족해야 합니다.

```dotenv
ENV=staging
STAGING_EC2_HOST=203.0.113.10
STAGING_PUBLIC_ORIGIN=http://203.0.113.10
CORS_ALLOWED_ORIGINS=http://203.0.113.10
COOKIE_DOMAIN=
```

`STAGING_SSH_KEY_PATH`에는 PEM 파일의 절대경로를 입력합니다. `DB_ADMIN_USER`,
`DB_MIGRATION_USER`, `DB_APP_USER`는 서로 다른 이름을 사용합니다.

OpenAI와 CLOVA 값에는 Staging에서 승인된 자격 증명만 입력합니다. 승인되지 않았다면
Provider 실호출을 성공 조건으로 기록하지 않습니다. `OCR_STRUCTURE_LLM_ENABLED`는
외부 전송 승인이 없는 상태에서 `false`를 유지합니다.

## 6. 배포

배포 대상 commit에서 필요한 검사를 먼저 통과시킨 뒤 실행합니다.

```bash
docker compose \
  --env-file envs/.staging.env \
  -f infra/docker/docker-compose.staging.yml \
  config --quiet

bash scripts/deploy-staging.sh
```

스크립트는 다음 순서를 사용합니다.

1. 환경변수, placeholder, DB 역할 분리와 immutable tag를 검증합니다.
2. 로컬에서 amd64 Backend·Frontend 이미지를 build하고 Docker Hub에 push합니다.
3. EC2의 `/home/ubuntu/finalproject-staging`에 mode `600` 환경파일과 Compose를 복사합니다.
4. PostgreSQL·Redis health check를 기다립니다.
5. 기존 Nginx·FastAPI를 멈추고 migration 전 DB dump를 생성합니다.
6. DB 역할을 재정렬한 뒤 Alembic migration을 실행합니다.
7. FastAPI health check가 성공한 뒤 Nginx를 시작합니다.
8. `deployment-evidence/<UTC timestamp>/`에 비민감 상태와 health 결과를 기록합니다.

Migration 또는 health check가 실패하면 신규 Nginx를 시작하지 않습니다. 스크립트는
rollback용 과거 이미지를 자동 삭제하지 않습니다.

## 7. 검증

로컬에서 다음을 확인합니다.

```bash
curl --fail http://EC2_PUBLIC_IP/healthz
curl --fail http://EC2_PUBLIC_IP/api/v1/health
```

EC2에서는 공개 port와 컨테이너 상태를 확인합니다.

```bash
cd /home/ubuntu/finalproject-staging
docker compose ps
docker compose images
sudo ss -lntp
```

성공 기준은 다음과 같습니다.

- Nginx와 FastAPI health check가 `healthy`입니다.
- `/api/v1/health`가 성공 응답을 반환합니다.
- React Router 하위 URL을 새로고침해도 Frontend가 표시됩니다.
- `5432`, `6379`, `8000`, `5173`이 host에 listen하지 않습니다.
- 합성 계정과 합성 처방전만 사용한 승인된 smoke가 동작합니다.
- PostgreSQL과 애플리케이션 컨테이너를 순서대로 재시작한 뒤에도 합성 DB·업로드
  volume의 데이터가 유지됩니다.

HTTP에서는 브라우저가 `Secure` refresh cookie를 저장하지 않으므로 token refresh 성공을
기록하지 않습니다. 현재 배포 스크립트도 오해를 막기 위해 `http://` origin만
허용합니다. 전체 인증 E2E는 HTTPS 전환 작업에서 별도로 검증합니다.

영속성 확인 시 one-shot migration 서비스를 무심코 다시 실행하지 않도록 전체
`docker compose restart` 대신 다음처럼 대상을 명시합니다.

```bash
docker compose restart postgres
docker compose up -d --wait postgres
docker compose restart redis fastapi nginx
docker compose up -d --wait redis fastapi nginx
```

## 8. Rollback 리허설

Production DB와 마찬가지로 migration downgrade는 실행하지 않습니다. 이전 이미지가
현재 schema와 호환되는지 담당 리뷰어가 확인한 뒤 애플리케이션 이미지만 되돌립니다.

1. EC2 `.env`의 `APP_VERSION`과 `FRONTEND_VERSION`을 직전 immutable tag로 변경합니다.
2. 현재 `.env`, image digest와 `deployment-evidence`를 보존합니다.
3. 아래 순서로 애플리케이션만 교체합니다.

```bash
cd /home/ubuntu/finalproject-staging
docker compose pull fastapi nginx
docker compose up -d --no-deps --wait fastapi
docker compose up -d --no-deps --wait nginx
docker compose ps
```

4. `/api/v1/health`와 합성 smoke를 다시 확인하고 결과를 배포 Issue 또는 PR에 기록합니다.
5. Schema 문제가 원인이면 downgrade하지 않고 후속 migration으로 forward-fix합니다.

## 9. 중지와 자원 정리

일시 중지는 EC2 인스턴스를 중지합니다. Public IPv4, EBS와 기타 자원의 Free Tier·credit
적용 여부는 중지 후에도 Billing에서 확인합니다. 리허설을 완전히 종료할 때는 보존할
합성 증빙을 확인한 뒤 EC2를 terminate하고 연결된 EBS volume, snapshot, Elastic IP가
남지 않았는지 각각 확인합니다.

Docker volume 삭제는 데이터 삭제이므로 단순 재배포 명령에 `down -v`를 사용하지
않습니다. 삭제가 필요하면 정확한 Staging volume과 합성 데이터임을 확인하고 별도
승인을 받아 수행합니다.
