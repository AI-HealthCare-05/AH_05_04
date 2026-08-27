from collections.abc import Generator

import pytest


@pytest.fixture(scope="session", autouse=True)
def initialize_database() -> Generator[None]:
    """Override the application database setup for pure Guide AI unit tests."""
    yield


@pytest.fixture(autouse=True)
def isolate_database() -> Generator[None]:
    """Override per-test database isolation because Guide AI tests do not use persistence."""
    yield
