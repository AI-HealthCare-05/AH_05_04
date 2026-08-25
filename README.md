# 다섯알 — 본인 중심 만성질환 복약관리

다섯알은 고혈압·제2형 당뇨병·이상지질혈증 등 만성질환 사용자가 처방전을 이해하고 자신의 복약 상태와 어려움을 관리할 수 있도록 돕는 서비스입니다. 처방전을 안전하게 구조화하고, 사용자가 확인한 처방을 기준으로 복약 안내와 질문 응답을 제공하며, 처방 변경이나 임상 판단이 필요한 상황에서는 약사·의료진 확인으로 연결하는 것을 목표로 합니다.

## 해결하려는 문제

- 처방전의 약품명·용량·횟수·복용법을 한눈에 이해하기 어렵습니다.
- 여러 복약 정보를 직접 입력하면 번거롭고 오류가 발생하기 쉽습니다.
- 같은 미복용이라도 망각, 일정 충돌, 복용법 이해 부족, 약에 대한 우려와 접근성 문제처럼 원인이 다릅니다.
- 일반적인 AI 답변은 사용자의 실제 처방과 다르거나 근거·안전 범위가 불분명할 수 있습니다.

다섯알은 처방전 OCR과 사용자 확인을 통해 입력 부담을 줄이고, 확정 처방 기반 안내에서 시작해 일정·Check-in·Barrier/Support와 OTC 병용 확인까지 하나의 반복 경험으로 연결합니다. 의료진의 반복 가능한 설명과 상담 준비를 보조하지만 진단, 처방 변경과 복용 중단을 대신 결정하지 않습니다.

## 목표 사용자와 핵심 가치

1차 대상은 고혈압·제2형 당뇨병·이상지질혈증 중 하나 이상을 진단받고 본인이 직접 처방약을 관리하는 40~60대 사용자입니다.

> 처방전을 올리면 복용해야 할 약과 일정을 쉽게 확인할 수 있고, 복약이 어려운 순간에는 상황에 맞는 도움을 받을 수 있습니다.

제품이 완성하려는 핵심 여정은 다음과 같습니다.

1. 처방전 업로드
2. OCR 결과 확인·수정과 처방 확정
3. 확정 처방 기반 복약 가이드와 챗봇
4. 사용자가 확인한 일정과 Check-in
5. 미복용 사유에 따른 Barrier/Support
6. OTC 성분 식별과 처방약 병용 확인

## 현재 구현과 목표 범위

Backend 기능 구현과 End-to-End 제품 MVP 완료를 구분합니다.

| 구분 | 2026-08-24 상태 |
| --- | --- |
| Backend MVP | 이메일 인증, 처방전 업로드, 같은 요청 안에서 완료되는 CLOVA OCR, 별도 OCR 필드 수정·처방 확정 API, OpenAI Guide·Chat 동기 API 구현 |
| Frontend 연결 | 회원가입·로그인, 처방전 업로드와 OCR 결과 요약까지 실제 API 연결 |
| Frontend 미연결 | OCR 필드 검수·처방 확정·Guide·Chat은 디자인 프로토타입 또는 미연결 상태 |
| Schema-only | 지식 문서·청크와 Guide·Chat citation 테이블은 존재하지만 실행 경로에는 미연결 |
| Post-MVP-1 목표 | 비동기 Job·Outbox·Redis Stream·Worker, 처방 버전, 일정·Check-in, Barrier/Support, OTC, 최소 RAG·Citation·Safety |
| 공개 상태 | 현재 Guide·Chat Production 공개 차단. Post-MVP-1 Track C·D·F도 별도 의료·약학·Privacy·Source 승인 전 공개 불가 |

현재 OCR endpoint의 `202 Accepted`는 queue 접수를 뜻하지 않습니다. FastAPI 요청 안에서 CLOVA 호출과 저장을 완료합니다. Guide와 Chat도 각각 동기 one-cycle `201 Created` 계약을 사용합니다. Redis와 `ai-worker` 서비스는 준비되어 있지만 현재 AI 요청 경로에는 연결되지 않았고 Worker는 placeholder로 종료합니다.

Post-MVP-1 계약 문서는 승인된 **목표 계약**이며 구현 완료 보고서가 아닙니다. 현재 상태의 상세 근거는 [시스템 아키텍처](docs/architecture.md), [AI 파이프라인](docs/ai-pipeline.md), [API 명세](docs/api.md), [데이터 구조](docs/data-schema.md), [테스트 전략](docs/testing.md)을 확인하세요.

## 의료 안전 원칙

- 사용자가 확인하기 전 OCR 값은 확정 처방이나 AI 안내의 기준으로 사용하지 않습니다.
- AI가 약명·용량·횟수·복용 시점·기간을 임의로 변경하지 않습니다.
- 근거가 없거나 상충하고 검증에 실패한 결과를 안전한 정상 답변으로 해석하지 않습니다.
- 실제 환자정보와 재식별 가능한 처방을 저장소 예시·fixture·로그에 포함하지 않습니다.
- 응급·고위험 가능성은 일반 안내보다 전문가 또는 응급 도움 연결을 우선합니다.

자세한 기준은 [보안 정책](SECURITY.md)과 [개인정보 및 의료 안전](docs/privacy-safety.md)을 따릅니다.

## 기술 구성

- **Backend**: FastAPI, Pydantic, SQLAlchemy asyncio, Alembic
- **Database**: PostgreSQL 17, `asyncpg`
- **AI Provider**: CLOVA OCR, OpenAI Responses API
- **Infrastructure**: Docker Compose, Nginx, Redis
- **Quality**: Ruff, Mypy, Pytest, Oxlint, TypeScript build

Redis와 `ai-worker` 서비스는 Compose에 준비되어 있지만 현재 MVP AI 요청 경로에는 연결되지 않습니다. AI Worker 컨테이너는 placeholder 진입점을 실행한 뒤 정상 종료합니다.

## 프로젝트 구조

```text
.
├── backend/            # FastAPI API, SQLAlchemy 모델·저장소, 동기 OCR·가이드·챗봇, Alembic
│   ├── app/
│   ├── alembic/
│   └── alembic.ini
├── ai_worker/          # Post-MVP 비동기 Worker 골격
├── frontend/           # 사용자 화면과 UX
├── docs/               # 아키텍처, API, 계약, AI 파이프라인, 테스트·배포 문서
├── evals/              # Post-MVP AI 평가 기준 준비 영역
├── knowledge/          # Post-MVP 승인 의료 지식 소스 준비 영역
├── tests/              # 계약·통합 테스트와 E2E 준비 영역
├── envs/               # 로컬·배포 환경변수 예시
├── infra/              # Docker·Nginx 배포 구성
├── scripts/            # CI·배포 스크립트
├── docker-compose.yml  # 로컬 개발 서비스 구성
└── pyproject.toml      # uv 의존성 및 도구 설정
```

## 사전 준비

- Python 3.13 이상
- [uv](https://docs.astral.sh/uv/)
- Docker와 Docker Compose

## 설치와 환경 설정

의존성을 설치합니다.

```bash
uv sync --all-groups
```

로컬 Docker Compose는 `envs/.local.env`를 사용합니다. 로컬 예시를 복사한 뒤 모든 placeholder를 실제 로컬 값으로 교체하고, 실제 환경 파일은 커밋하지 않습니다. 운영 환경은 `envs/example.prod.env`를 참고해 별도의 `envs/.prod.env`를 준비합니다.

```bash
cp envs/example.local.env envs/.local.env
```

최소한 DB 설정과 사용하는 외부 제공자의 자격증명을 확인합니다. `OPENAI_MODEL`과 `OPENAI_TIMEOUT_SECONDS` 등 운영 기준값은 코드 기본값만으로 승인하지 않고 [배포 가이드](docs/deployment.md)에 실제 값과 확인 결과를 기록합니다.

## 실행

Backend와 지원 인프라를 빌드하고 실행합니다. 이 Compose 파일에는 Frontend 서비스가 포함되지 않습니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  up -d --build
```

Frontend는 별도 터미널에서 실행합니다.

```bash
cd frontend
pnpm install
pnpm dev
```

주요 서비스명은 다음과 같습니다.

| 서비스 | 역할 | 현재 동작 |
| --- | --- | --- |
| `fastapi` | API 서버 | `http://localhost:8000/api/docs` |
| `postgres` | PostgreSQL 17 | 영속 데이터 저장 |
| `migrate` | Alembic migration | 실행 완료 후 정상 종료 |
| `redis` | Redis | 기동되지만 현재 MVP AI 경로에서는 미사용 |
| `ai-worker` | Post-MVP Worker 골격 | placeholder 로그 후 정상 종료 |
| `nginx` | 리버스 프록시 | `http://localhost/api/docs` |

필요한 서비스만 실행할 수도 있습니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  up -d --build fastapi

docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  up -d --build ai-worker
```

로컬 Python 환경에서 API 서버를 직접 실행하려면 DB와 필요한 외부 설정을 준비한 뒤 다음 명령을 사용합니다. `Config`는 현재 작업 디렉터리 기준으로 `.env`를 찾으므로, 저장소 루트에서 실행 위치만 `--app-dir`로 지정합니다.

```bash
uv run --env-file envs/.local.env \
  uvicorn --app-dir backend app.main:app --reload
```

## Database migration

전체 Compose 실행 시 `migrate` 서비스가 PostgreSQL health check 후 다음 명령을 한 번 실행합니다.

```bash
uv run --no-sync alembic upgrade head
```

로컬 Python 환경에서는 다음 명령을 사용합니다.

```bash
uv run alembic -c backend/alembic.ini upgrade head
```

## 테스트와 품질 검사

저장소 완료 기준은 [CONTRIBUTING.md](CONTRIBUTING.md)와 [테스트 전략](docs/testing.md)을 따릅니다.

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy backend/app ai_worker
bash scripts/ci/run_test.sh

cd frontend
pnpm lint
pnpm build
```

Python 기본 테스트 명령은 `backend/app`, `tests/contract`, `ai_worker/tests/core`를 실행합니다. OpenAI 실호출 smoke, `tests/integration`, E2E와 AI 평가가 기본 CI에서 자동 실행되는 것은 아닙니다. 정확한 범위는 [테스트 전략](docs/testing.md)을 확인하세요.

문서만 변경한 경우에는 렌더링·링크·범위와 전체 diff를 검토하고 `git diff --check`를 실행합니다.

## 개발 가이드

- API 경로와 오류 계약은 [API 명세](docs/api.md)를 따릅니다.
- 공유 DTO·상태·오류 의미를 변경하면 관련 [계약 문서](docs/contracts/README.md), 구현과 계약·통합 테스트를 함께 갱신합니다.
- DB 모델은 `backend/app/models/`에 SQLAlchemy 모델로 정의하고 Alembic migration을 추가합니다.
- 현재 동기 AI 구현은 `backend/app/services/ocr.py`, `backend/app/services/guide_ai/`, `backend/app/services/chat_ai/`에 있습니다.
- Post-MVP 비동기 작업은 계약과 전환 조건을 승인한 뒤 `ai_worker/tasks/`에 구현합니다.
- 비밀정보와 실제 환자 정보는 저장소에 포함하지 않습니다. [SECURITY.md](SECURITY.md)와 [개인정보 및 의료 안전](docs/privacy-safety.md)을 따릅니다.
