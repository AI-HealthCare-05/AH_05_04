import logging
import os

from app.core.config import Config
from app.core.logger import setup_logger


def get_config() -> Config:
    if os.environ.get("RELEASE_VALIDATION_RUNNER") == "1":
        # Runner와 Backend의 validation 허용 설정은 별도 process 경계입니다.
        # Runner가 자신의 guard용 환경변수를 설정해도 Backend 허용 상태로 해석하지 않습니다.
        return Config(_env_file=None, RELEASE_VALIDATION_ALLOWED=False)  # type: ignore[call-arg]
    # DB 설정은 BaseSettings가 .env에서 읽는다.
    return Config()  # type: ignore[call-arg]


def get_logger() -> logging.Logger:
    # 앱 전역에서 사용할 로거
    return setup_logger()


config = get_config()
default_logger = get_logger()
