"""The Writer process must not silently fall back to Runtime or administrator secrets."""

import json

import pytest

from ai_worker.admin.catalog_writer import main, writer_url


@pytest.mark.parametrize(
    "secret",
    [
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "SOURCE_MANAGEMENT_PASSWORD",
        "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
        "CANDIDATE_INDEX_BUILDER_PASSWORD",
    ],
)
def test_rejects_mixed_credentials(secret):
    with pytest.raises(ValueError, match="isolated credential"):
        writer_url({secret: "synthetic-only"})


def test_requires_explicit_catalog_login_and_redacts_password():
    with pytest.raises(ValueError, match="incomplete"):
        writer_url({})
    values = {
        "CATALOG_WRITER_" + key: value
        for key, value in {
            "HOST": "localhost",
            "PORT": "5432",
            "NAME": "synthetic",
            "USER": "catalog_writer",
            "PASSWORD": "synthetic-private",
        }.items()
    }
    url = writer_url(values)
    assert url.username == "catalog_writer"
    assert "synthetic-private" not in repr(url)
    with pytest.raises(ValueError, match="port"):
        writer_url({**values, "CATALOG_WRITER_PORT": "0"})


def test_catalog_writer_main_fails_closed_without_source_authority(capsys, monkeypatch):
    for forbidden in (
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "SOURCE_MANAGEMENT_PASSWORD",
        "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
        "CANDIDATE_INDEX_BUILDER_PASSWORD",
    ):
        monkeypatch.delenv(forbidden, raising=False)

    # Valid isolated env
    for k, v in {
        "CATALOG_WRITER_HOST": "localhost",
        "CATALOG_WRITER_PORT": "5432",
        "CATALOG_WRITER_NAME": "test",
        "CATALOG_WRITER_USER": "writer",
        "CATALOG_WRITER_PASSWORD": "secret",
    }.items():
        monkeypatch.setenv(k, v)

    # Without args: fail-closed with BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY
    exit_code = main([])
    assert exit_code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["execution_status"] == "BLOCKED"
    assert payload["blocker_reason"] == "BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY"

    # Even with args in Phase A: fail-closed
    exit_code = main(["--source-snapshot-id", "00000000-0000-0000-0000-000000000001"])
    assert exit_code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["execution_status"] == "BLOCKED"
    assert payload["blocker_reason"] == "BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY"

    # With mixed credentials: fail with FAILED
    monkeypatch.setenv("DB_PASSWORD", "leaked")
    exit_code = main([])
    assert exit_code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["execution_status"] == "FAILED"
