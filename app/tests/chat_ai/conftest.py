from collections.abc import Generator

import pytest


@pytest.fixture(scope="session", autouse=True)
def initialize_database() -> Generator[None]:
    """Keep pure chat AI unit tests independent from the application database."""
    yield


@pytest.fixture(autouse=True)
def isolate_database() -> Generator[None]:
    """Avoid opening a database transaction for chat AI unit tests."""
    yield
