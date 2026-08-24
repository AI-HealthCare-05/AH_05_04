# AI Healthcare 복약 가이드·챗봇

FastAPI, SQLAlchemy asyncio와 MySQL을 기반으로 처방전 OCR·검수, 복약 가이드와 복약 챗봇을 제공하는 프로젝트입니다. Python 의존성은 `uv`, 로컬 서비스는 Docker Compose로 관리합니다.

## 현재 Backend MVP 범위

- 처방전 업로드와 CLOVA OCR 실행·결과 검수
- 사용자 확정 처방 생성·조회
- 확정 처방 기반 복약 가이드 동기 one-cycle 생성
- 확정 처방 기반 복약 챗봇 동기 one-cycle 응답
- OpenAI timeout·가용성·응답 처리 실패 매핑과 민감정보 비노출

RAG, 출처 인용, Citation/NLI 검증, OTC 성분·상호작용 기능, AI 응답 품질 평가와 비동기 AI Worker는 **Post-MVP** 범위입니다. 자세한 상태는 [시스템 아키텍처](docs/architecture.md), [AI 파이프라인](docs/ai-pipeline.md), [테스트 전략](docs/testing.md)을 참고하세요.

Frontend는 현재 회원가입·로그인, 처방전 업로드와 OCR 결과 요약까지 Backend API에 연결되어 있습니다. OCR 필드 검수·처방 확정·가이드·챗봇 화면은 아직 전체 사용자 여정으로 연결되지 않았으므로 Backend 기능 구현과 End-to-End MVP 완료를 구분합니다.

## 기술 구성

- **Backend**: FastAPI, Pydantic, SQLAlchemy asyncio, Alembic
- **Database**: MySQL 8.0, `asyncmy`
- **AI Provider**: CLOVA OCR, OpenAI Responses API
- **Infrastructure**: Docker Compose, Nginx, Redis
- **Quality**: Ruff, Mypy, Pytest, Oxlint, TypeScript build

Redis와 `ai-worker` 서비스는 Compose에 준비되어 있지만 현재 MVP AI 요청 경로에는 연결되지 않습니다. AI Worker 컨테이너는 placeholder 진입점을 실행한 뒤 정상 종료합니다.

## 프로젝트 구조

```text
.
├── app/                # FastAPI API, SQLAlchemy 모델·저장소, 동기 OCR·가이드·챗봇
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

로컬 Docker Compose는 저장소 루트의 `.env`를 읽습니다. 예시를 복사한 뒤 모든 `replace-with-...` 값을 실제 로컬 값으로 교체하고, `.env`는 커밋하지 않습니다. 예시 placeholder를 그대로 둔 상태는 실행·배포 가능한 설정이 아닙니다.

```bash
cp envs/example.local.env .env
```

최소한 DB 설정과 사용하는 외부 제공자의 자격증명을 확인합니다. `OPENAI_MODEL`과 `OPENAI_TIMEOUT_SECONDS` 등 운영 기준값은 코드 기본값만으로 승인하지 않고 [배포 가이드](docs/deployment.md)에 실제 값과 확인 결과를 기록합니다.

## 실행

Backend와 지원 인프라를 빌드하고 실행합니다. 이 Compose 파일에는 Frontend 서비스가 포함되지 않습니다.

```bash
docker compose up -d --build
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
| `mysql` | MySQL 8.0 | 영속 데이터 저장 |
| `migrate` | Alembic migration | 실행 완료 후 정상 종료 |
| `redis` | Redis | 기동되지만 현재 MVP AI 경로에서는 미사용 |
| `ai-worker` | Post-MVP Worker 골격 | placeholder 로그 후 정상 종료 |
| `nginx` | 리버스 프록시 | `http://localhost/api/docs` |

필요한 서비스만 실행할 수도 있습니다.

```bash
docker compose up -d --build fastapi
docker compose up -d --build ai-worker
```

로컬 Python 환경에서 API 서버를 직접 실행하려면 DB와 필요한 외부 설정을 준비한 뒤 다음 명령을 사용합니다.

```bash
uv run uvicorn app.main:app --reload
```

## Database migration

전체 Compose 실행 시 `migrate` 서비스가 MySQL health check 후 다음 명령을 한 번 실행합니다.

```bash
uv run --no-sync alembic upgrade head
```

로컬 Python 환경에서는 다음 명령을 사용합니다.

```bash
uv run alembic upgrade head
```

## 테스트와 품질 검사

저장소 완료 기준은 [CONTRIBUTING.md](CONTRIBUTING.md)와 [테스트 전략](docs/testing.md)을 따릅니다.

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy app ai_worker
bash scripts/ci/run_test.sh

cd frontend
pnpm lint
pnpm build
```

Python 기본 테스트 명령은 `app`과 `tests/contract`를 실행합니다. OpenAI 실호출 smoke, `tests/integration`, E2E와 AI 평가가 기본 CI에서 자동 실행되는 것은 아닙니다. 정확한 범위는 [테스트 전략](docs/testing.md)을 확인하세요.

문서만 변경한 경우에는 렌더링·링크·범위와 전체 diff를 검토하고 `git diff --check`를 실행합니다.

## 개발 가이드

- API 경로와 오류 계약은 [API 명세](docs/api.md)를 따릅니다.
- 공유 DTO·상태·오류 의미를 변경하면 관련 [계약 문서](docs/contracts/README.md), 구현과 계약·통합 테스트를 함께 갱신합니다.
- DB 모델은 `app/models/`에 SQLAlchemy 모델로 정의하고 Alembic migration을 추가합니다.
- 현재 동기 AI 구현은 `app/services/ocr.py`, `app/services/guide_ai/`, `app/services/chat_ai/`에 있습니다.
- Post-MVP 비동기 작업은 계약과 전환 조건을 승인한 뒤 `ai_worker/tasks/`에 구현합니다.
- 비밀정보와 실제 환자 정보는 저장소에 포함하지 않습니다. [SECURITY.md](SECURITY.md)와 [개인정보 및 의료 안전](docs/privacy-safety.md)을 따릅니다.
