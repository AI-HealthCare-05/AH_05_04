"""Unit tests for candidate_index_builder management command."""

import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.commands import candidate_index_builder as command


@pytest.mark.parametrize(
    "secret",
    [
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "SOURCE_MANAGEMENT_PASSWORD",
        "CATALOG_WRITER_PASSWORD",
        "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
    ],
)
def test_rejects_mixed_credentials(secret: str) -> None:
    with pytest.raises(ValueError, match="isolated credential"):
        command.builder_url({secret: "synthetic-only"})


def test_requires_explicit_candidate_login_and_redacts_password() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        command.builder_url({})
    values = {
        "CANDIDATE_INDEX_BUILDER_" + key: value
        for key, value in {
            "HOST": "localhost",
            "PORT": "5432",
            "NAME": "synthetic",
            "USER": "candidate_builder",
            "PASSWORD": "synthetic-private",
        }.items()
    }
    url = command.builder_url(values)
    assert url.username == "candidate_builder"
    assert "synthetic-private" not in repr(url)
    with pytest.raises(ValueError, match="port"):
        command.builder_url({**values, "CANDIDATE_INDEX_BUILDER_PORT": "0"})


@pytest.mark.asyncio
async def test_candidate_index_builder_fails_closed_without_config_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_set_id = uuid4()
    mock_session = AsyncMock()
    mock_session_factory = Mock()
    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_session
    mock_context.__aexit__.return_value = None
    mock_session_factory.return_value = mock_context

    mock_catalog_repo = AsyncMock()
    mock_catalog_build = Mock()
    mock_catalog_repo.load_build.return_value = mock_catalog_build

    monkeypatch.setattr(command, "SqlAlchemyCatalogBuildRepository", Mock(return_value=mock_catalog_repo))
    monkeypatch.setattr(command, "SqlAlchemyCatalogApprovalVerifier", Mock(return_value=Mock()))

    result = await command.execute_candidate_index_builder_command(
        mock_session_factory,
        catalog_set_id=catalog_set_id,
        config=None,  # Absence of config authority
    )

    assert result["execution_status"] == "BLOCKED"
    assert result["blocker_reason"] == "BLOCKED_BY_CANDIDATE_CONFIG_AUTHORITY"


def test_candidate_index_builder_main_cli(capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    for forbidden in (
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "SOURCE_MANAGEMENT_PASSWORD",
        "CATALOG_WRITER_PASSWORD",
        "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
    ):
        monkeypatch.delenv(forbidden, raising=False)

    for k, v in {
        "CANDIDATE_INDEX_BUILDER_HOST": "localhost",
        "CANDIDATE_INDEX_BUILDER_PORT": "5432",
        "CANDIDATE_INDEX_BUILDER_NAME": "test",
        "CANDIDATE_INDEX_BUILDER_USER": "cand_builder",
        "CANDIDATE_INDEX_BUILDER_PASSWORD": "secret",
    }.items():
        monkeypatch.setenv(k, v)

    exit_code = command.main([])
    assert exit_code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["execution_status"] == "BLOCKED"
    assert payload["blocker_reason"] == "BLOCKED_BY_CANDIDATE_CONFIG_AUTHORITY"

    # With mixed credentials: fail with FAILED
    monkeypatch.setenv("DB_PASSWORD", "leaked")
    exit_code = command.main([])
    assert exit_code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["execution_status"] == "FAILED"
