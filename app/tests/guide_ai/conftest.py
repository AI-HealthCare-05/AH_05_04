from collections.abc import Generator

import pytest


@pytest.fixture(scope="session", autouse=True)
def initialize() -> Generator[None]:
    """Keep pure guide AI unit tests independent from the application database."""
    yield
