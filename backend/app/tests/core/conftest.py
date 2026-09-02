import pytest


@pytest.fixture(scope="session")
def initialize_database() -> None:
    """Keep pure core unit tests independent from PostgreSQL."""


@pytest.fixture
def isolate_database() -> None:
    """Keep pure core unit tests independent from PostgreSQL."""
