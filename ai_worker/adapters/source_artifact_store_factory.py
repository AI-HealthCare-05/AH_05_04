"""Worker 설정에서 Source Artifact 저장소를 조립합니다."""

from pathlib import Path

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.adapters.s3_private_source_artifact_store import (
    S3ObjectClient,
    S3SourceArtifactStoreConfig,
    create_s3_source_artifact_store,
)
from ai_worker.core.config import Config
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactStore


def create_source_artifact_store(
    config: Config,
    *,
    s3_client: S3ObjectClient | None = None,
) -> RawArtifactStore:
    """비활성 상태를 자동 local fallback 없이 fail-closed합니다."""

    if config.SOURCE_ARTIFACT_STORAGE_BACKEND == "DISABLED":
        raise ValueError("Source artifact storage is disabled.")
    if config.SOURCE_ARTIFACT_STORAGE_BACKEND == "LOCAL_PRIVATE":
        if s3_client is not None:
            raise ValueError("LOCAL_PRIVATE Source artifact storage cannot use an S3 client.")
        if config.SOURCE_ARTIFACT_LOCAL_ROOT is None:
            raise ValueError("LOCAL_PRIVATE Source artifact storage root is missing.")
        return LocalPrivateSourceArtifactStore(Path(config.SOURCE_ARTIFACT_LOCAL_ROOT))

    if config.SOURCE_ARTIFACT_S3_BUCKET is None:
        raise ValueError("S3_PRIVATE Source artifact storage bucket is missing.")
    if config.SOURCE_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION is None:
        raise ValueError("S3_PRIVATE Source artifact storage encryption is missing.")
    return create_s3_source_artifact_store(
        S3SourceArtifactStoreConfig(
            bucket=config.SOURCE_ARTIFACT_S3_BUCKET,
            server_side_encryption=config.SOURCE_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION,
            prefix=config.SOURCE_ARTIFACT_S3_PREFIX,
            region_name=config.SOURCE_ARTIFACT_S3_REGION,
            endpoint_url=config.SOURCE_ARTIFACT_S3_ENDPOINT_URL,
            kms_key_id=config.SOURCE_ARTIFACT_S3_KMS_KEY_ID,
        ),
        client=s3_client,
    )
