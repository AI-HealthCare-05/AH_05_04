"""The Writer process must not silently fall back to Runtime or administrator secrets."""

import pytest

from ai_worker.admin.catalog_writer import writer_url


@pytest.mark.parametrize(
    "secret",
    [
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "SOURCE_MANAGEMENT_PASSWORD",
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
