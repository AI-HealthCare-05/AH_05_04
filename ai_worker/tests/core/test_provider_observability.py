from datetime import UTC, datetime
from uuid import uuid4

from ai_worker.core.provider_observability import create_worker_provider_call_context
from ai_worker.schemas.messages import WorkerMessage
from provider_contracts.observability import DeploymentEnvironment, ProviderCallContext


def _message() -> WorkerMessage:
    now = datetime.now(UTC)
    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(uuid4()),
            "job_type": "OCR",
            "domain_type": "OCR_JOB",
            "domain_id": str(uuid4()),
            "attempt": 1,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": "0123456789abcdef0123456789abcdef",
        }
    )


def test_worker_context_preserves_validated_trace_and_environment() -> None:
    message = _message()

    context = create_worker_provider_call_context(
        message=message,
        environment=DeploymentEnvironment.STAGING,
    )

    assert isinstance(context, ProviderCallContext)
    assert context.trace_id == message.trace_id
    assert context.environment is DeploymentEnvironment.STAGING
    assert context.validation_run_id is None
    assert context.validation_enabled is False
