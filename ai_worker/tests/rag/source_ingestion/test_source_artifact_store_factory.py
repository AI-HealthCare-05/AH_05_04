from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.adapters.s3_private_source_artifact_store import S3PrivateSourceArtifactStore
from ai_worker.adapters.source_artifact_store_factory import create_source_artifact_store
from ai_worker.core.config import Config
from provider_contracts.observability import DeploymentEnvironment

_REQUIRED_SETTINGS: dict[str, Any] = {
    "ENV": DeploymentEnvironment.LOCAL,
    "DB_HOST": "127.0.0.1",
    "DB_NAME": "test",
    "DB_USER": "worker",
    "DB_PASSWORD": "worker-password",
    "CLOVA_OCR_INVOKE_URL": "https://clova.test/ocr",
    "CLOVA_OCR_SECRET": "synthetic-clova-secret",
    "STORAGE_DIR": "/tmp/medical-documents",
}


def _config(**overrides: object) -> Config:
    return Config(  # type: ignore[call-arg]
        _env_file=None,
        **{**_REQUIRED_SETTINGS, **overrides},
    )


def test_factory_fails_closed_when_source_artifact_storage_is_disabled() -> None:
    with pytest.raises(ValueError, match="disabled"):
        create_source_artifact_store(_config())


def test_factory_creates_local_private_store(tmp_path: Path) -> None:
    store = create_source_artifact_store(
        _config(
            SOURCE_ARTIFACT_STORAGE_BACKEND="LOCAL_PRIVATE",
            SOURCE_ARTIFACT_LOCAL_ROOT=str(tmp_path / "source-artifacts"),
        )
    )

    assert isinstance(store, LocalPrivateSourceArtifactStore)


def test_factory_creates_s3_store_with_injected_client() -> None:
    client = MagicMock()
    store = create_source_artifact_store(
        _config(
            SOURCE_ARTIFACT_STORAGE_BACKEND="S3_PRIVATE",
            SOURCE_ARTIFACT_S3_BUCKET="private-source-artifacts",
            SOURCE_ARTIFACT_S3_REGION="ap-northeast-2",
            SOURCE_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION="AES256",
        ),
        s3_client=client,
    )

    assert isinstance(store, S3PrivateSourceArtifactStore)


def test_factory_rejects_s3_client_for_local_backend(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot use an S3 client"):
        create_source_artifact_store(
            _config(
                SOURCE_ARTIFACT_STORAGE_BACKEND="LOCAL_PRIVATE",
                SOURCE_ARTIFACT_LOCAL_ROOT=str(tmp_path / "source-artifacts"),
            ),
            s3_client=MagicMock(),
        )
