from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_worker.adapters.openai_text_embedding import OpenAITextEmbeddingAdapter
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.text_embedding import (
    TextEmbeddingFailure,
    TextEmbeddingFailureReason,
    TextEmbeddingSuccess,
)

_MODEL_REF = "openai:text-embedding-3-large"
_MODEL_VERSION = "text-embedding-3-large"
_DIMENSION = 1536
_ADAPTER_REF = ImmutableArtifactRef(
    artifact_code="openai-text-embedding-adapter",
    version="1",
    content_sha256="0" * 64,
)


def _make_embedding_response(
    vector: list[float] | tuple[float, ...],
    model: str = "text-embedding-3-large",
    index: int = 0,
) -> MagicMock:
    item = MagicMock()
    item.embedding = list(vector)
    item.index = index
    response = MagicMock()
    response.data = [item]
    response.model = model
    return response


@pytest.mark.asyncio
async def test_openai_embedding_success() -> None:
    fake_client = MagicMock()
    vec = [0.1] * _DIMENSION
    fake_response = _make_embedding_response(vec)
    fake_client.embeddings.create = AsyncMock(return_value=fake_response)

    adapter = OpenAITextEmbeddingAdapter(
        client=fake_client,
        adapter_artifact_ref=_ADAPTER_REF,
    )

    text = SensitiveText("타이레놀 복용법")
    result = await adapter.embed(
        text,
        model_ref=_MODEL_REF,
        model_version=_MODEL_VERSION,
        dimension=_DIMENSION,
    )

    assert isinstance(result, TextEmbeddingSuccess)
    assert len(result.embedding) == 1536
    assert result.adapter_artifact_ref == _ADAPTER_REF
    fake_client.embeddings.create.assert_awaited_once_with(
        input="타이레놀 복용법",
        model="text-embedding-3-large",
        dimensions=1536,
        encoding_format="float",
    )


@pytest.mark.asyncio
async def test_openai_embedding_configuration_mismatch() -> None:
    fake_client = MagicMock()
    adapter = OpenAITextEmbeddingAdapter(client=fake_client, adapter_artifact_ref=_ADAPTER_REF)
    text = SensitiveText("테스트 쿼리")

    # Mismatched model_ref
    res1 = await adapter.embed(
        text,
        model_ref="wrong-model-ref",
        model_version=_MODEL_VERSION,
        dimension=_DIMENSION,
    )
    assert res1 == TextEmbeddingFailure(TextEmbeddingFailureReason.CONFIGURATION_MISMATCH)

    # Mismatched model_version
    res2 = await adapter.embed(
        text,
        model_ref=_MODEL_REF,
        model_version="wrong-version",
        dimension=_DIMENSION,
    )
    assert res2 == TextEmbeddingFailure(TextEmbeddingFailureReason.CONFIGURATION_MISMATCH)

    # Mismatched dimension
    res3 = await adapter.embed(
        text,
        model_ref=_MODEL_REF,
        model_version=_MODEL_VERSION,
        dimension=3072,
    )
    assert res3 == TextEmbeddingFailure(TextEmbeddingFailureReason.CONFIGURATION_MISMATCH)


@pytest.mark.asyncio
async def test_openai_embedding_rejects_dimension_mismatch_in_response() -> None:
    fake_client = MagicMock()
    # Response has 3072 dimensions instead of 1536
    fake_response = _make_embedding_response([0.1] * 3072)
    fake_client.embeddings.create = AsyncMock(return_value=fake_response)

    adapter = OpenAITextEmbeddingAdapter(client=fake_client, adapter_artifact_ref=_ADAPTER_REF)
    res = await adapter.embed(
        SensitiveText("테스트"),
        model_ref=_MODEL_REF,
        model_version=_MODEL_VERSION,
        dimension=_DIMENSION,
    )
    assert res == TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)


@pytest.mark.asyncio
async def test_openai_embedding_rejects_zero_and_nan_vectors() -> None:
    fake_client = MagicMock()
    adapter = OpenAITextEmbeddingAdapter(client=fake_client, adapter_artifact_ref=_ADAPTER_REF)

    # Zero vector
    fake_client.embeddings.create = AsyncMock(return_value=_make_embedding_response([0.0] * _DIMENSION))
    res_zero = await adapter.embed(
        SensitiveText("테스트"),
        model_ref=_MODEL_REF,
        model_version=_MODEL_VERSION,
        dimension=_DIMENSION,
    )
    assert res_zero == TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)

    # NaN vector
    nan_vec = [0.1] * (_DIMENSION - 1) + [float("nan")]
    fake_client.embeddings.create = AsyncMock(return_value=_make_embedding_response(nan_vec))
    res_nan = await adapter.embed(
        SensitiveText("테스트"),
        model_ref=_MODEL_REF,
        model_version=_MODEL_VERSION,
        dimension=_DIMENSION,
    )
    assert res_nan == TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)


@pytest.mark.asyncio
async def test_openai_embedding_rejects_multiple_or_reordered_items() -> None:
    fake_client = MagicMock()
    adapter = OpenAITextEmbeddingAdapter(client=fake_client, adapter_artifact_ref=_ADAPTER_REF)

    # Multiple items in data
    resp_multi = MagicMock()
    item1 = MagicMock(embedding=[0.1] * _DIMENSION, index=0)
    item2 = MagicMock(embedding=[0.2] * _DIMENSION, index=1)
    resp_multi.data = [item1, item2]
    resp_multi.model = "text-embedding-3-large"
    fake_client.embeddings.create = AsyncMock(return_value=resp_multi)

    res_multi = await adapter.embed(
        SensitiveText("테스트"),
        model_ref=_MODEL_REF,
        model_version=_MODEL_VERSION,
        dimension=_DIMENSION,
    )
    assert res_multi == TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)

    # Reordered item (index != 0)
    resp_reordered = MagicMock()
    item_reordered = MagicMock(embedding=[0.1] * _DIMENSION, index=1)
    resp_reordered.data = [item_reordered]
    resp_reordered.model = "text-embedding-3-large"
    fake_client.embeddings.create = AsyncMock(return_value=resp_reordered)

    res_reordered = await adapter.embed(
        SensitiveText("테스트"),
        model_ref=_MODEL_REF,
        model_version=_MODEL_VERSION,
        dimension=_DIMENSION,
    )
    assert res_reordered == TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)


@pytest.mark.asyncio
async def test_openai_embedding_handles_dependency_error_without_leaking_message() -> None:
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(side_effect=RuntimeError("internal sensitive provider token error"))

    adapter = OpenAITextEmbeddingAdapter(client=fake_client, adapter_artifact_ref=_ADAPTER_REF)
    res = await adapter.embed(
        SensitiveText("테스트"),
        model_ref=_MODEL_REF,
        model_version=_MODEL_VERSION,
        dimension=_DIMENSION,
    )
    assert res == TextEmbeddingFailure(TextEmbeddingFailureReason.DEPENDENCY_ERROR)
