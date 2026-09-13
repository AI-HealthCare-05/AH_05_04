import base64
import hashlib
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import ANY, Stubber

from ai_worker.adapters.s3_private_source_artifact_store import (
    S3ObjectClient,
    S3PrivateSourceArtifactStore,
    S3SourceArtifactStoreConfig,
    create_s3_source_artifact_store,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata


def _metadata(content: bytes) -> RawArtifactMetadata:
    return RawArtifactMetadata(
        artifact_key="page-0001.json",
        raw_checksum=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        content_type="application/json",
    )


def _head_response(metadata: RawArtifactMetadata) -> dict[str, object]:
    return {
        "ContentLength": metadata.byte_size,
        "ContentType": metadata.content_type,
        "Metadata": {"raw-sha256": metadata.raw_checksum, "immutable": "true"},
        "ServerSideEncryption": "AES256",
        "ChecksumSHA256": base64.b64encode(bytes.fromhex(metadata.raw_checksum)).decode("ascii"),
    }


def _store(client: S3ObjectClient) -> S3PrivateSourceArtifactStore:
    return S3PrivateSourceArtifactStore(
        client=client,
        config=S3SourceArtifactStoreConfig(
            bucket="private-source-artifacts",
            server_side_encryption="AES256",
            prefix="environment/source",
            region_name="ap-northeast-2",
        ),
    )


def test_puts_verified_content_as_conditionally_created_private_object(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    metadata = _metadata(content)
    client = MagicMock()
    client.put_object.return_value = {}
    client.head_object.return_value = _head_response(metadata)

    stored = _store(client).put_verified(page_number=1, file_path=source, metadata=metadata)

    put = client.put_object.call_args.kwargs
    assert put["Bucket"] == "private-source-artifacts"
    assert put["Key"] == f"environment/source/sha256/{metadata.raw_checksum[:2]}/{metadata.raw_checksum}.artifact"
    assert put["IfNoneMatch"] == "*"
    assert put["ChecksumAlgorithm"] == "SHA256"
    assert put["ServerSideEncryption"] == "AES256"
    assert put["Metadata"] == {"raw-sha256": metadata.raw_checksum, "immutable": "true"}
    assert stored.storage_backend == "S3_PRIVATE"
    assert stored.object_key == put["Key"]
    client.head_object.assert_called_once_with(
        Bucket="private-source-artifacts",
        Key=stored.object_key,
        ChecksumMode="ENABLED",
    )


def test_put_request_matches_installed_s3_sdk_contract(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    metadata = _metadata(content)
    checksum = base64.b64encode(bytes.fromhex(metadata.raw_checksum)).decode("ascii")
    object_key = f"environment/source/sha256/{metadata.raw_checksum[:2]}/{metadata.raw_checksum}.artifact"
    client = boto3.client(
        "s3",
        region_name="ap-northeast-2",
        endpoint_url="https://storage.example",
        aws_access_key_id="synthetic-access-key",
        aws_secret_access_key="synthetic-secret-key",
    )
    with Stubber(client) as stubber:
        stubber.add_response(
            "put_object",
            {},
            {
                "Bucket": "private-source-artifacts",
                "Key": object_key,
                "Body": ANY,
                "ContentLength": metadata.byte_size,
                "ContentType": metadata.content_type,
                "ChecksumAlgorithm": "SHA256",
                "ChecksumSHA256": checksum,
                "IfNoneMatch": "*",
                "Metadata": {"raw-sha256": metadata.raw_checksum, "immutable": "true"},
                "ServerSideEncryption": "AES256",
            },
        )
        stubber.add_response(
            "head_object",
            {
                **_head_response(metadata),
                "ChecksumSHA256": checksum,
            },
            {
                "Bucket": "private-source-artifacts",
                "Key": object_key,
                "ChecksumMode": "ENABLED",
            },
        )

        stored = _store(cast(S3ObjectClient, client)).put_verified(
            page_number=1,
            file_path=source,
            metadata=metadata,
        )

    assert stored.object_key == object_key


def test_reuses_existing_object_only_after_metadata_verification(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    metadata = _metadata(content)
    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "PreconditionFailed", "Message": "exists"}},
        "PutObject",
    )
    client.head_object.return_value = _head_response(metadata)

    stored = _store(client).put_verified(page_number=1, file_path=source, metadata=metadata)

    assert stored.metadata == metadata
    client.head_object.assert_called_once()


def test_rejects_changed_source_before_remote_write(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(b"changed")
    client = MagicMock()

    with pytest.raises(ValueError, match="byte size mismatch|checksum mismatch"):
        _store(client).put_verified(
            page_number=1,
            file_path=source,
            metadata=_metadata(b"expected"),
        )

    client.put_object.assert_not_called()


def test_rejects_existing_object_with_different_checksum_metadata(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    metadata = _metadata(content)
    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "412", "Message": "exists"}},
        "PutObject",
    )
    client.head_object.return_value = {
        **_head_response(metadata),
        "Metadata": {"raw-sha256": "0" * 64, "immutable": "true"},
    }

    with pytest.raises(ValueError, match="checksum metadata mismatch"):
        _store(client).put_verified(page_number=1, file_path=source, metadata=metadata)


def test_rejects_existing_object_without_immutable_marker(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    metadata = _metadata(content)
    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "412", "Message": "exists"}},
        "PutObject",
    )
    client.head_object.return_value = {
        **_head_response(metadata),
        "Metadata": {"raw-sha256": metadata.raw_checksum},
    }

    with pytest.raises(ValueError, match="immutability metadata mismatch"):
        _store(client).put_verified(page_number=1, file_path=source, metadata=metadata)


def test_rejects_existing_object_without_provider_checksum(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    metadata = _metadata(content)
    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "412", "Message": "exists"}},
        "PutObject",
    )
    head_response = _head_response(metadata)
    del head_response["ChecksumSHA256"]
    client.head_object.return_value = head_response

    with pytest.raises(ValueError, match="object checksum mismatch"):
        _store(client).put_verified(page_number=1, file_path=source, metadata=metadata)


def test_sanitizes_provider_failure_without_exposing_provider_message(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "credential-detail"}},
        "PutObject",
    )

    with pytest.raises(ValueError) as raised:
        _store(client).put_verified(page_number=1, file_path=source, metadata=_metadata(content))

    assert str(raised.value) == "Source artifact object could not be preserved."
    assert "credential-detail" not in str(raised.value)
    client.head_object.assert_not_called()


def test_sanitizes_transport_failure_without_exposing_endpoint(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    client = MagicMock()
    client.put_object.side_effect = EndpointConnectionError(endpoint_url="https://private-storage.example")

    with pytest.raises(ValueError) as raised:
        _store(client).put_verified(page_number=1, file_path=source, metadata=_metadata(content))

    assert str(raised.value) == "Source artifact object could not be preserved."
    assert "private-storage.example" not in str(raised.value)


def test_rejects_existing_object_without_required_encryption(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    metadata = _metadata(content)
    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "412", "Message": "exists"}},
        "PutObject",
    )
    head_response = _head_response(metadata)
    del head_response["ServerSideEncryption"]
    client.head_object.return_value = head_response

    with pytest.raises(ValueError, match="encryption mismatch"):
        _store(client).put_verified(page_number=1, file_path=source, metadata=metadata)


def test_uses_explicit_kms_key_and_verifies_existing_object(tmp_path: Path) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "source.json"
    source.write_bytes(content)
    metadata = _metadata(content)
    client = MagicMock()
    client.put_object.return_value = {}
    client.head_object.return_value = {
        **_head_response(metadata),
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": "synthetic-kms-key-id",
    }
    store = S3PrivateSourceArtifactStore(
        client=client,
        config=S3SourceArtifactStoreConfig(
            bucket="private-source-artifacts",
            server_side_encryption="aws:kms",
            kms_key_id="synthetic-kms-key-id",
        ),
    )

    store.put_verified(page_number=1, file_path=source, metadata=metadata)

    put = client.put_object.call_args.kwargs
    assert put["ServerSideEncryption"] == "aws:kms"
    assert put["SSEKMSKeyId"] == "synthetic-kms-key-id"


@pytest.mark.parametrize(
    "config_kwargs",
    [
        {"server_side_encryption": "aws:kms"},
        {"server_side_encryption": "AES256", "kms_key_id": "unexpected-key"},
    ],
)
def test_rejects_incomplete_or_mixed_encryption_settings(config_kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="KMS key ID"):
        S3SourceArtifactStoreConfig(
            bucket="private-source-artifacts",
            **config_kwargs,
        )


@pytest.mark.parametrize(
    "endpoint_url",
    [
        "http://storage.example",
        "https://user:secret@storage.example",
        "https://storage.example?token=secret",
        "https://storage.example#fragment",
    ],
)
def test_rejects_insecure_or_credential_bearing_endpoint(endpoint_url: str) -> None:
    with pytest.raises(ValueError, match="credential 없는 HTTPS"):
        S3SourceArtifactStoreConfig(
            bucket="private-source-artifacts",
            server_side_encryption="AES256",
            endpoint_url=endpoint_url,
        )


def test_factory_uses_standard_credential_chain_without_explicit_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    boto_client = MagicMock(return_value=client)
    monkeypatch.setattr("ai_worker.adapters.s3_private_source_artifact_store.boto3.client", boto_client)
    config = S3SourceArtifactStoreConfig(
        bucket="private-source-artifacts",
        server_side_encryption="AES256",
        region_name="ap-northeast-2",
        endpoint_url="https://storage.example",
    )

    store = create_s3_source_artifact_store(config)

    assert isinstance(store, S3PrivateSourceArtifactStore)
    kwargs = boto_client.call_args.kwargs
    assert kwargs["region_name"] == "ap-northeast-2"
    assert kwargs["endpoint_url"] == "https://storage.example"
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
    assert "aws_session_token" not in kwargs
