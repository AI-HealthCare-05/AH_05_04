import logging
import os

from app.core.config import Config
from app.core.logger import setup_logger


def get_config() -> Config:
    if os.environ.get("RELEASE_VALIDATION_RUNNER") == "1":
        return Config(_env_file=None)  # type: ignore[call-arg]
    # DB 설정은 BaseSettings가 .env에서 읽는다.
    return Config()  # type: ignore[call-arg]


def get_logger() -> logging.Logger:
    # 앱 전역에서 사용할 로거
    return setup_logger()


config = get_config()
default_logger = get_logger()
