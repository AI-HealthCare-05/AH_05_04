import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

from provider_contracts.observability import (
    DeploymentEnvironment,
    Provider,
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderOperation,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRACE_ID = "0123456789abcdef0123456789abcdef"
VALIDATION_RUN_ID = UUID("61a10000-0000-4000-8000-000000000003")


def test_shared_contract_import_does_not_initialize_backend_or_worker() -> None:
    clean_environment = {
        key: value for key, value in os.environ.items() if key not in {"DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"}
    }
    probe = """
import sys
from provider_contracts.observability import (
    DeploymentEnvironment,
    Provider,
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderOperation,
)

ProviderCallContext(
    trace_id="0123456789abcdef0123456789abcdef",
    validation_run_id=None,
    environment=DeploymentEnvironment.LOCAL,
    validation_enabled=False,
)
ProviderCallDescriptor(
    provider=Provider.OPENAI,
    operation=ProviderOperation.CHAT_GENERATION,
    prompt_version="chat-prompt-v2",
)
assert "app.core" not in sys.modules
assert "ai_worker.core" not in sys.modules
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=clean_environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_worker_context_factory_does_not_initialize_worker_settings() -> None:
    clean_environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ENV", "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"}
    }
    probe = """
import sys

import ai_worker.core as worker_core
from ai_worker.core.provider_observability import create_worker_provider_call_context
from ai_worker.schemas.messages import WorkerMessage
from provider_contracts.observability import DeploymentEnvironment

message = WorkerMessage.model_validate(
    {
        "schema_version": "1.0",
        "event_id": "61a10000-0000-4000-8000-000000000001",
        "event_kind": "JOB_EXECUTE",
        "job_id": "61a10000-0000-4000-8000-000000000002",
        "job_type": "OCR",
        "domain_type": "OCR_JOB",
        "domain_id": "61a10000-0000-4000-8000-000000000003",
        "attempt": 1,
        "available_at": "2026-01-01T00:00:00+00:00",
        "enqueued_at": "2026-01-01T00:00:00+00:00",
        "trace_id": "0123456789abcdef0123456789abcdef",
    }
)
context = create_worker_provider_call_context(
    message=message,
    environment=DeploymentEnvironment.PRODUCTION,
)

assert not hasattr(worker_core, "default_logger")
assert context.trace_id == message.trace_id
assert context.environment is DeploymentEnvironment.PRODUCTION
assert context.validation_run_id is None
assert context.validation_enabled is False
assert "app.core" not in sys.modules
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=clean_environment,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "environment",
    list(DeploymentEnvironment),
)
def test_context_accepts_general_worker_trace_in_every_environment(
    environment: DeploymentEnvironment,
) -> None:
    context = ProviderCallContext(
        trace_id=TRACE_ID,
        validation_run_id=None,
        environment=environment,
        validation_enabled=False,
    )

    assert context.environment is environment


@pytest.mark.parametrize("trace_id", ["a" * 31, "a" * 33, "g" * 32])
def test_context_rejects_invalid_trace_id(trace_id: str) -> None:
    with pytest.raises(ValueError, match="128-bit hexadecimal"):
        ProviderCallContext(
            trace_id=trace_id,
            validation_run_id=None,
            environment=DeploymentEnvironment.LOCAL,
            validation_enabled=False,
        )


@pytest.mark.parametrize(
    ("environment", "validation_enabled"),
    [
        (DeploymentEnvironment.LOCAL, False),
        (DeploymentEnvironment.STAGING, True),
        (DeploymentEnvironment.PRODUCTION, True),
    ],
)
def test_context_rejects_validation_run_outside_enabled_local_validation(
    environment: DeploymentEnvironment,
    validation_enabled: bool,
) -> None:
    with pytest.raises(ValueError, match="enabled local validation"):
        ProviderCallContext(
            trace_id=TRACE_ID,
            validation_run_id=VALIDATION_RUN_ID,
            environment=environment,
            validation_enabled=validation_enabled,
        )


def test_context_accepts_enabled_local_validation_run() -> None:
    context = ProviderCallContext(
        trace_id=TRACE_ID,
        validation_run_id=VALIDATION_RUN_ID,
        environment=DeploymentEnvironment.LOCAL,
        validation_enabled=True,
    )

    assert context.validation_run_id == VALIDATION_RUN_ID


def test_descriptor_accepts_clova_prescription_recognition_without_prompt() -> None:
    descriptor = ProviderCallDescriptor(
        provider=Provider.CLOVA_OCR,
        operation=ProviderOperation.PRESCRIPTION_RECOGNITION,
        prompt_version=None,
    )

    assert descriptor.prompt_version is None


@pytest.mark.parametrize(
    ("operation", "prompt_version"),
    [
        (ProviderOperation.PRESCRIPTION_RECOGNITION, "unexpected-prompt"),
        (ProviderOperation.OCR_STRUCTURING, None),
    ],
)
def test_descriptor_rejects_invalid_clova_shape(
    operation: ProviderOperation,
    prompt_version: str | None,
) -> None:
    with pytest.raises(ValueError, match="CLOVA OCR descriptor"):
        ProviderCallDescriptor(
            provider=Provider.CLOVA_OCR,
            operation=operation,
            prompt_version=prompt_version,
        )


def test_descriptor_rejects_openai_prescription_recognition() -> None:
    with pytest.raises(ValueError, match="cannot use prescription recognition"):
        ProviderCallDescriptor(
            provider=Provider.OPENAI,
            operation=ProviderOperation.PRESCRIPTION_RECOGNITION,
            prompt_version="unexpected-prompt",
        )


@pytest.mark.parametrize("prompt_version", [None, ""])
def test_descriptor_rejects_openai_without_prompt_version(prompt_version: str | None) -> None:
    with pytest.raises(ValueError, match="requires a prompt version"):
        ProviderCallDescriptor(
            provider=Provider.OPENAI,
            operation=ProviderOperation.GUIDE_GENERATION,
            prompt_version=prompt_version,
        )
