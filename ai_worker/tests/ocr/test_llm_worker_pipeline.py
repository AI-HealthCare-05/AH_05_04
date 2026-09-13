import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from ai_worker.adapters import openai_ocr_structurer
from ai_worker.core.config import Config
from ai_worker.core.runtime_assembly import create_clova_ocr_engine, create_clova_ocr_provider
from ai_worker.tasks.ocr.handler import OcrProviderSchemaError, OcrProviderTimeoutError
from ocr_runtime.clova_engine import ClovaOcrEngine
from ocr_runtime.llm.prompt import PROMPT_VERSION
from ocr_runtime.llm.schemas import GeneratedMedication, GeneratedPrescriptionDraft, GeneratedSourceValue
from provider_contracts.ocr import OcrRecognitionResult, RawRecognizedField


def config(tmp_path, **changes):
    values = dict(
        ENV="local",
        DB_HOST="localhost",
        DB_NAME="test",
        DB_USER="worker",
        DB_PASSWORD="synthetic",
        CLOVA_OCR_INVOKE_URL="https://clova.test/ocr",
        CLOVA_OCR_SECRET="synthetic",
        STORAGE_DIR=str(tmp_path),
        OCR_STRUCTURE_LLM_ENABLED=True,
        OPENAI_API_KEY="synthetic-test-key",
    )
    return Config(_env_file=None, **(values | changes))


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    (tmp_path / "synthetic.png").write_bytes(b"synthetic-provider-stub")
    clova = AsyncMock(
        return_value=OcrRecognitionResult(
            raw_fields=[
                RawRecognizedField(
                    raw_value="합성의약품에이정",
                    confidence_score=0.99,
                    center_x=10,
                    center_y=30,
                )
            ]
        )
    )
    monkeypatch.setattr(ClovaOcrEngine, "_recognize_provider", clova)
    parse = AsyncMock(
        return_value=SimpleNamespace(
            status="completed",
            model="actual-synthetic-model",
            output=[],
            output_parsed=GeneratedPrescriptionDraft(
                medications=[
                    GeneratedMedication(
                        medication_name=GeneratedSourceValue(value="합성의약품에이정", source_ids=[1]),
                    )
                ]
            ),
        )
    )
    clients = []

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.responses = SimpleNamespace(parse=parse)
            self.closed = False
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

    monkeypatch.setattr(openai_ocr_structurer, "AsyncOpenAI", Client)
    return clova, parse, clients


async def recognize(tmp_path, **changes):
    provider = create_clova_ocr_provider(config(tmp_path, **changes))
    return await provider.recognize(
        object_key="synthetic.png",
        file_mime_type="image/png",
        trace_id="a" * 32,
        deadline=time.monotonic() + 5,
    )


async def test_real_worker_factory_runs_llm_and_preserves_metadata(tmp_path, pipeline):
    clova, parse, clients = pipeline
    result = await recognize(tmp_path)
    assert clova.await_count == parse.await_count == 1
    assert result.engine_name == "CLOVA_OCR"
    assert result.model_version == "actual-synthetic-model"
    assert result.prompt_version == PROMPT_VERSION
    assert any(f.field_type == "MEDICATION_NAME" and f.raw_value == "합성의약품에이정" for f in result.fields)
    assert parse.call_args.kwargs["store"] is False
    assert clients[0].kwargs["max_retries"] == 0
    assert clients[0].closed


async def test_disabled_flag_never_creates_openai_client(tmp_path, pipeline):
    _, parse, clients = pipeline
    result = await recognize(tmp_path, OCR_STRUCTURE_LLM_ENABLED=False, OPENAI_API_KEY="")
    assert result.model_version is result.prompt_version is None
    assert parse.await_count == 0
    assert clients == []


async def test_grounding_failure_is_not_replaced_with_rule_success(tmp_path, pipeline):
    _, parse, clients = pipeline
    parse.return_value.output_parsed.medications[0].medication_name.source_ids = [999]
    with pytest.raises(OcrProviderSchemaError):
        await recognize(tmp_path)
    assert clients[0].closed


async def test_timeout_closes_client_without_success(tmp_path, pipeline):
    _, parse, clients = pipeline

    async def slow(**kwargs):
        await asyncio.sleep(5)

    parse.side_effect = slow
    with pytest.raises(OcrProviderTimeoutError):
        await recognize(tmp_path, OCR_STRUCTURE_TIMEOUT_SECONDS=0.01)
    assert clients[0].closed


@pytest.mark.parametrize(
    "changes",
    [
        {"OPENAI_API_KEY": ""},
        {"OCR_STRUCTURE_MODEL": " "},
        {"OCR_STRUCTURE_TIMEOUT_SECONDS": 40},
        {"OCR_STRUCTURE_TIMEOUT_SECONDS": float("inf")},
    ],
)
def test_invalid_activation_is_rejected_before_worker_start(tmp_path, changes):
    with pytest.raises(ValidationError):
        config(tmp_path, **changes)


def test_engine_repr_does_not_expose_key(tmp_path):
    engine = create_clova_ocr_engine(config(tmp_path), trace_id="b" * 32)
    assert "synthetic-test-key" not in repr(engine)


@pytest.mark.parametrize("environment", ["local", "staging", "production"])
async def test_llm_activation_is_available_in_each_environment(tmp_path, environment, pipeline):
    clova, parse, clients = pipeline
    result = await recognize(tmp_path, ENV=environment, REDIS_PASSWORD="test-redis-453-credential")
    assert clova.await_count == parse.await_count == 1
    assert result.model_version == "actual-synthetic-model"
    assert clients[0].closed
