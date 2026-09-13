"""S3 호환 비공개 저장소에 Source 원본을 불변 보존합니다."""

import base64
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    StoredRawArtifact,
    validate_artifact_binding,
    verify_raw_artifact,
)

_STORAGE_BACKEND = "S3_PRIVATE"
_PRECONDITION_FAILURE_CODES = frozenset({"412", "PreconditionFailed"})


class S3ObjectClient(Protocol):
    def put_object(self, **kwargs: object) -> dict[str, Any]: ...

    def head_object(self, **kwargs: object) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class S3SourceArtifactStoreConfig:
    """비밀값을 포함하지 않는 S3 저장 위치 설정입니다."""

    bucket: str
    server_side_encryption: Literal["AES256", "aws:kms"]
    prefix: str = "source-artifacts"
    region_name: str | None = None
    endpoint_url: str | None = None
    kms_key_id: str | None = None

    def __post_init__(self) -> None:
        if self.bucket != self.bucket.strip() or not self.bucket or len(self.bucket) > 255:
            raise ValueError("Source artifact S3 bucket 형식이 올바르지 않습니다.")

        normalized_prefix = self.prefix.strip("/")
        prefix_parts = PurePosixPath(normalized_prefix).parts
        if not normalized_prefix or ".." in prefix_parts or len(normalized_prefix) > 400:
            raise ValueError("Source artifact S3 prefix 형식이 올바르지 않습니다.")
        object.__setattr__(self, "prefix", normalized_prefix)

        if self.region_name is not None:
            normalized_region = self.region_name.strip()
            if not normalized_region:
                raise ValueError("Source artifact S3 region은 비어 있을 수 없습니다.")
            object.__setattr__(self, "region_name", normalized_region)
        if self.endpoint_url is not None:
            _validate_endpoint_url(self.endpoint_url)
        if self.server_side_encryption == "aws:kms":
            if self.kms_key_id is None or not self.kms_key_id.strip():
                raise ValueError("aws:kms Source artifact storage에는 KMS key ID가 필요합니다.")
        elif self.kms_key_id is not None:
            raise ValueError("AES256 Source artifact storage에는 KMS key ID를 설정할 수 없습니다.")


def create_s3_source_artifact_store(
    config: S3SourceArtifactStoreConfig,
    *,
    client: S3ObjectClient | None = None,
) -> "S3PrivateSourceArtifactStore":
    """실행 역할 또는 표준 AWS 환경 주입으로 S3 client를 조립합니다."""

    if client is None:
        client = cast(
            S3ObjectClient,
            boto3.client(
                "s3",
                region_name=config.region_name,
                endpoint_url=config.endpoint_url,
                config=BotoConfig(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            ),
        )
    return S3PrivateSourceArtifactStore(client=client, config=config)


class S3PrivateSourceArtifactStore:
    """조건부 생성과 SHA-256 검증으로 내용 주소 객체를 보존합니다."""

    def __init__(self, *, client: S3ObjectClient, config: S3SourceArtifactStoreConfig) -> None:
        self._client = client
        self._config = config

    def put_verified(
        self,
        *,
        page_number: int | None,
        file_path: Path,
        metadata: RawArtifactMetadata,
        artifact_kind: IngestionArtifactKind = IngestionArtifactKind.RAW_RESPONSE,
        reject_code: str | None = None,
        parser_location: str | None = None,
    ) -> StoredRawArtifact:
        validate_artifact_binding(
            page_number=page_number,
            artifact_kind=artifact_kind,
            reject_code=reject_code,
            parser_location=parser_location,
        )
        verify_raw_artifact(file_path=file_path, metadata=metadata)
        object_key = self._object_key(metadata.raw_checksum)
        put_request: dict[str, object] = {
            "Bucket": self._config.bucket,
            "Key": object_key,
            "ContentLength": metadata.byte_size,
            "ContentType": metadata.content_type,
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": _base64_checksum(metadata.raw_checksum),
            "IfNoneMatch": "*",
            "Metadata": {
                "raw-sha256": metadata.raw_checksum,
                "immutable": "true",
            },
            "ServerSideEncryption": self._config.server_side_encryption,
        }
        if self._config.kms_key_id is not None:
            put_request["SSEKMSKeyId"] = self._config.kms_key_id

        try:
            with file_path.open("rb") as body:
                self._client.put_object(Body=body, **put_request)
        except ClientError as error:
            if _client_error_code(error) not in _PRECONDITION_FAILURE_CODES:
                raise ValueError("Source artifact object could not be preserved.") from None
        except BotoCoreError:
            raise ValueError("Source artifact object could not be preserved.") from None
        except OSError:
            raise ValueError("Raw artifact could not be read or preserved.") from None

        self._verify_existing_object(object_key=object_key, metadata=metadata)
        return StoredRawArtifact(
            page_number=page_number,
            metadata=metadata,
            storage_backend=_STORAGE_BACKEND,
            object_key=object_key,
            artifact_kind=artifact_kind,
            reject_code=reject_code,
            parser_location=parser_location,
        )

    def _object_key(self, raw_checksum: str) -> str:
        return f"{self._config.prefix}/sha256/{raw_checksum[:2]}/{raw_checksum}.artifact"

    def _verify_existing_object(self, *, object_key: str, metadata: RawArtifactMetadata) -> None:
        try:
            response = self._client.head_object(
                Bucket=self._config.bucket,
                Key=object_key,
                ChecksumMode="ENABLED",
            )
        except (BotoCoreError, ClientError):
            raise ValueError("Source artifact object could not be verified.") from None

        remote_metadata = response.get("Metadata")
        if not isinstance(remote_metadata, dict):
            raise ValueError("Source artifact object metadata mismatch.")
        if response.get("ContentLength") != metadata.byte_size:
            raise ValueError("Source artifact object byte size mismatch.")
        if response.get("ContentType") != metadata.content_type:
            raise ValueError("Source artifact object content type mismatch.")
        if response.get("ServerSideEncryption") != self._config.server_side_encryption:
            raise ValueError("Source artifact object encryption mismatch.")
        if self._config.kms_key_id is not None and response.get("SSEKMSKeyId") != self._config.kms_key_id:
            raise ValueError("Source artifact object KMS key mismatch.")
        if remote_metadata.get("raw-sha256") != metadata.raw_checksum:
            raise ValueError("Source artifact object checksum metadata mismatch.")
        if remote_metadata.get("immutable") != "true":
            raise ValueError("Source artifact object immutability metadata mismatch.")

        remote_checksum = response.get("ChecksumSHA256")
        if remote_checksum != _base64_checksum(metadata.raw_checksum):
            raise ValueError("Source artifact object checksum mismatch.")


def _base64_checksum(raw_checksum: str) -> str:
    return base64.b64encode(bytes.fromhex(raw_checksum)).decode("ascii")


def _client_error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _validate_endpoint_url(endpoint_url: str) -> None:
    parsed = urlsplit(endpoint_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Source artifact S3 endpoint는 credential 없는 HTTPS URL이어야 합니다.")
