import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import StatementError

from app.core.db.databases import engine


@pytest.fixture(scope="session")
def initialize_database() -> None:
    """Keep SQL logging safety isolated from the repository's MySQL schema fixture."""


@pytest.fixture
def isolate_database() -> None:
    """Keep SQL logging safety isolated from the repository's MySQL transaction fixture."""


def test_production_engine_hides_bound_parameters() -> None:
    assert engine.sync_engine.hide_parameters is True


def test_statement_failure_hides_synthetic_question_and_medication_bind_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    synthetic_question = "SYNTHETIC_QUESTION_SENTINEL_38"
    synthetic_medication = "SYNTHETIC_MEDICATION_SENTINEL_38"
    hidden_marker = "SQL parameters hidden due to hide_parameters=True"
    safety_engine = create_engine(
        "sqlite://",
        echo=True,
        hide_parameters=engine.sync_engine.hide_parameters,
    )
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine.Engine")

    try:
        with pytest.raises(StatementError) as captured:
            with safety_engine.connect() as connection:
                connection.execute(
                    text("SELECT :question, :medication FROM synthetic_missing_table_for_parameter_safety"),
                    {
                        "question": synthetic_question,
                        "medication": synthetic_medication,
                    },
                )
    finally:
        safety_engine.dispose()

    exception_text = str(captured.value)
    log_text = caplog.text
    for sentinel in (synthetic_question, synthetic_medication):
        assert sentinel not in exception_text
        assert sentinel not in log_text
    assert hidden_marker in exception_text
    assert hidden_marker in log_text
