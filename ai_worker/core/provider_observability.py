from ai_worker.schemas.messages import WorkerMessage
from provider_contracts.observability import (
    DeploymentEnvironment,
    ProviderCallContext,
)


def create_worker_provider_call_context_from_trace_id(
    *,
    trace_id: str,
    environment: DeploymentEnvironment,
) -> ProviderCallContext:
    return ProviderCallContext(
        trace_id=trace_id,
        validation_run_id=None,
        environment=environment,
        validation_enabled=False,
    )


def create_worker_provider_call_context(
    *,
    message: WorkerMessage,
    environment: DeploymentEnvironment,
) -> ProviderCallContext:
    return create_worker_provider_call_context_from_trace_id(
        trace_id=message.trace_id,
        environment=environment,
    )
