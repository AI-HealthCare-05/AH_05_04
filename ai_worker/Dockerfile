FROM python:3.13-slim

WORKDIR /app

# 1. uv 설치 (공식 이미지에서 복사해오는 가장 깔끔한 방법)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 2. 의존성 패키지 관리 파일을 컨테이너 내부에 복사합니다.
COPY pyproject.toml uv.lock ./

# 3. pyproject.toml에 명시된 의존성 패키지 중에서 'app' 그룹만 설치합니다.
RUN uv sync --group ai --no-dev --frozen

# 4. AI Worker 소스 코드를 Python 패키지 경로로 복사합니다.
COPY ./ai_worker ./ai_worker
COPY ./provider_contracts ./provider_contracts

# 5. AI Worker 진입점을 실행합니다.
CMD ["uv", "run", "--no-sync", "python", "-m", "ai_worker.main"]
