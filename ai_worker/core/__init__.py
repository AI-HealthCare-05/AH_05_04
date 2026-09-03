import logging

from ai_worker.core.config import Config
from ai_worker.core.logger import setup_logger


def get_config() -> Config:
    # DB 설정은 BaseSettings가 .env에서 읽는다.
    return Config()  # type: ignore[call-arg]


def get_logger() -> logging.Logger:
    # 앱 전역에서 사용할 로거
    return setup_logger()
