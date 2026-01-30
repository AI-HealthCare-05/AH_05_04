import logging

from app.core.config import Config
from app.core.logger import setup_logger


def get_config() -> Config:
    return Config()


def get_logger() -> logging.Logger:
    # 앱 전역에서 사용할 로거
    return setup_logger()


config = get_config()
default_logger = get_logger()
