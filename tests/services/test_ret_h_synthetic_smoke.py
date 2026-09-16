from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.release_validation.ret_h_synthetic_smoke import (
    AWS_SMOKE_NOT_EXECUTED,
    STATUS_SUCCESS,
    check_cloudwatch_sentinel,
    inspect_dedicated_smoke_dlq,
    run_ret_h_smoke,
    run_verification_transaction,
    verify_task_container_image,
)


@pytest.mark.asyncio
async def test_smoke_local_preflight_returns_not_executed() -> None:
    receipt = await run_ret_h_smoke(
        mode="local-preflight",
        commit_sha="abcd123",
        image_repo_digest="sha256:123456",
    )
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.mode == "local-preflight"
    assert "not configured" in (receipt.error_message or "")


@pytest.mark.asyncio
async def test_smoke_missing_aws_creds_returns_not_executed() -> None:
    with patch("app.release_validation.ret_h_synthetic_smoke.check_aws_credentials_available", return_value=False):
        receipt = await run_ret_h_smoke(
            mode="staging-live",
            commit_sha="abcd123",
            image_repo_digest="sha256:123456",
        )
        assert receipt.status == AWS_SMOKE_NOT_EXECUTED
        assert "not configured" in (receipt.error_message or "")


def test_verify_task_container_image_success() -> None:
    ecs_mock = MagicMock()
    ecs_mock.describe_tasks.return_value = {
        "tasks": [
            {
                "containers": [
                    {
                        "imageDigest": "sha256:abc123def456",
                        "image": "123456789.dkr.ecr.ap-northeast-2.amazonaws.com/ah:commit-12345",
                    }
                ]
            }
        ]
    }
    commit, digest = verify_task_container_image(
        ecs_client=ecs_mock,
        cluster="staging-cluster",
        task_arn="arn:aws:ecs:ap-northeast-2:123456789:task/123",
        expected_commit_sha="commit-12345",
        expected_image_digest="sha256:abc123def456",
    )
    assert commit == "commit-12345"
    assert digest == "sha256:abc123def456"


def test_verify_task_container_image_digest_mismatch() -> None:
    ecs_mock = MagicMock()
    ecs_mock.describe_tasks.return_value = {
        "tasks": [
            {
                "containers": [
                    {
                        "imageDigest": "sha256:unexpected_digest",
                        "image": "123456789.dkr.ecr.ap-northeast-2.amazonaws.com/ah:latest",
                    }
                ]
            }
        ]
    }
    with pytest.raises(ValueError, match="Image digest mismatch"):
        verify_task_container_image(
            ecs_client=ecs_mock,
            cluster="staging-cluster",
            task_arn="arn:aws:ecs:ap-northeast-2:123456789:task/123",
            expected_commit_sha=None,
            expected_image_digest="sha256:expected_digest",
        )


def test_check_cloudwatch_sentinel_found() -> None:
    logs_mock = MagicMock()
    logs_mock.filter_log_events.return_value = {
        "events": [{"message": "retrieval run started run_id=run-123", "timestamp": 12345678}]
    }
    found = check_cloudwatch_sentinel(
        logs_client=logs_mock,
        log_group_name="/ecs/staging-ah-ai-worker",
        run_id="run-123",
        start_time_epoch_ms=10000,
    )
    assert found is True
    logs_mock.filter_log_events.assert_called_once_with(
        logGroupName="/ecs/staging-ah-ai-worker",
        startTime=10000,
        filterPattern="run-123",
    )


def test_inspect_dedicated_smoke_dlq_non_destructive() -> None:
    sqs_mock = MagicMock()
    sqs_mock.receive_message.return_value = {"Messages": [{"MessageId": "msg-1", "Body": "test"}]}
    result = inspect_dedicated_smoke_dlq(
        sqs_client=sqs_mock,
        smoke_dlq_url="https://sqs.ap-northeast-2.amazonaws.com/123/smoke-dlq",
    )
    assert result["dlq_inspected"] is True
    assert result["messages_peeked"] == 1
    # Ensure delete_message was NOT called
    sqs_mock.delete_message.assert_not_called()


@dataclass
class DummyRunRow:
    id: str
    status: str
    variant: str
    receipt_hash: str


@dataclass
class DummySignalRow:
    retrieval_run_id: str
    retrieval_method: str


@dataclass
class DummyHitRow:
    retrieval_run_id: str
    selected: bool


@pytest.mark.asyncio
async def test_run_verification_transaction_success() -> None:
    run_id = str(uuid4())
    expected_hash = "f" * 64

    run_row = DummyRunRow(id=run_id, status="COMPLETED", variant="RET-H", receipt_hash=expected_hash)
    signals = [
        DummySignalRow(retrieval_run_id=run_id, retrieval_method="LEXICAL"),
        DummySignalRow(retrieval_run_id=run_id, retrieval_method="DENSE"),
    ]
    hits = [
        DummyHitRow(retrieval_run_id=run_id, selected=True),
        DummyHitRow(retrieval_run_id=run_id, selected=False),
    ]

    session_mock = AsyncMock()

    class MockResult:
        def __init__(self, data: Any):
            self._data = data

        def first(self) -> Any:
            return self._data[0] if isinstance(self._data, list) else self._data

        def all(self) -> list[Any]:
            return self._data if isinstance(self._data, list) else [self._data]

    session_mock.execute.side_effect = [
        MockResult(run_row),
        MockResult(signals),
        MockResult(hits),
    ]

    class SessionFactory:
        def __call__(self) -> Any:
            class ContextManager:
                async def __aenter__(self) -> Any:
                    return session_mock

                async def __aexit__(self, *args: Any) -> None:
                    pass

            return ContextManager()

    details = await run_verification_transaction(
        session_factory=SessionFactory(),
        run_id=run_id,
        expected_receipt_hash=expected_hash,
    )
    assert details["run_id"] == run_id
    assert details["status"] == "COMPLETED"
    assert details["variant"] == "RET-H"
    assert details["signals_count"] == 2
    assert details["hits_count"] == 2
    assert details["selected_hits_count"] == 1


@pytest.mark.asyncio
async def test_run_verification_transaction_fails_on_unfinalized_status() -> None:
    run_id = str(uuid4())
    run_row = DummyRunRow(id=run_id, status="RUNNING", variant="RET-H", receipt_hash="a" * 64)

    session_mock = AsyncMock()

    class MockResult:
        def __init__(self, data: Any):
            self._data = data

        def first(self) -> Any:
            return self._data

        def all(self) -> list[Any]:
            return [self._data]

    session_mock.execute.return_value = MockResult(run_row)

    class SessionFactory:
        def __call__(self) -> Any:
            class ContextManager:
                async def __aenter__(self) -> Any:
                    return session_mock

                async def __aexit__(self, *args: Any) -> None:
                    pass

            return ContextManager()

    with pytest.raises(AssertionError, match="expected COMPLETED, got RUNNING"):
        await run_verification_transaction(
            session_factory=SessionFactory(),
            run_id=run_id,
            expected_receipt_hash="a" * 64,
        )


@pytest.mark.asyncio
async def test_run_ret_h_smoke_full_success_flow() -> None:
    from ai_worker.tasks.rag.retrieval_runtime import (
        HybridRetrieveOutcome,
        RetrievalExecutionStatus,
    )

    run_id = str(uuid4())
    receipt_hash = "e" * 64

    persisted_receipt_mock = MagicMock(run_id=run_id, receipt_hash=receipt_hash)
    fake_outcome = HybridRetrieveOutcome(
        status=RetrievalExecutionStatus.SUCCEEDED,
        persisted_receipt=persisted_receipt_mock,
        search_receipt=MagicMock(),
        gate_outcome=MagicMock(),
        message="Success",
    )

    ecs_mock = MagicMock()
    ecs_mock.describe_tasks.return_value = {
        "tasks": [
            {
                "containers": [
                    {
                        "imageDigest": "sha256:abc123",
                        "image": "ecr/ah:sha-test",
                    }
                ]
            }
        ]
    }
    logs_mock = MagicMock()
    logs_mock.filter_log_events.return_value = {"events": [{"timestamp": 123}]}
    sqs_mock = MagicMock()
    sqs_mock.receive_message.return_value = {"Messages": []}

    stale_check = AsyncMock(return_value=True)
    locator_check = AsyncMock(return_value=True)

    with (
        patch(
            "app.release_validation.ret_h_synthetic_smoke.run_execution_transaction",
            new=AsyncMock(return_value=fake_outcome),
        ),
        patch(
            "app.release_validation.ret_h_synthetic_smoke.run_verification_transaction",
            new=AsyncMock(return_value={"verified": True}),
        ),
    ):
        receipt = await run_ret_h_smoke(
            mode="staging-live",
            commit_sha="sha-test",
            image_repo_digest="sha256:abc123",
            task_arn="arn:aws:ecs:task-1",
            cluster="staging",
            smoke_dlq_url="https://sqs.staging/smoke-dlq",
            log_group_name="/ecs/staging",
            ecs_client=ecs_mock,
            logs_client=logs_mock,
            sqs_client=sqs_mock,
            session_factory=MagicMock(),
            search_port=MagicMock(),
            run_store=MagicMock(),
            hybrid_retrieve_request=MagicMock(),
            stale_verify_callable=stale_check,
            locator_mismatch_verify_callable=locator_check,
        )

    assert receipt.status == STATUS_SUCCESS
    assert receipt.execution_transaction_verified is True
    assert receipt.verification_transaction_verified is True
    assert receipt.evidence_gate_stale_fail_closed_verified is True
    assert receipt.evidence_gate_locator_fail_closed_verified is True
    assert receipt.cloudwatch_logs_verified is True
    assert receipt.sqs_dlq_verified is True
    assert receipt.retrieval_run_id == run_id


@pytest.mark.asyncio
async def test_run_ret_h_smoke_delegates_to_execution_fn() -> None:
    from ai_worker.tasks.rag.retrieval_runtime import (
        HybridRetrieveOutcome,
        RetrievalExecutionStatus,
    )

    run_id = str(uuid4())
    receipt_hash = "f" * 64
    persisted_receipt_mock = MagicMock(run_id=run_id, receipt_hash=receipt_hash)
    fake_outcome = HybridRetrieveOutcome(
        status=RetrievalExecutionStatus.SUCCEEDED,
        persisted_receipt=persisted_receipt_mock,
        search_receipt=MagicMock(),
        gate_outcome=MagicMock(),
        message="Success",
    )

    mock_exec_fn = AsyncMock(return_value=fake_outcome)

    with (
        patch("app.release_validation.ret_h_synthetic_smoke.check_aws_credentials_available", return_value=True),
        patch(
            "app.release_validation.ret_h_synthetic_smoke.run_verification_transaction",
            new=AsyncMock(return_value={"verified": True}),
        ),
    ):
        receipt = await run_ret_h_smoke(
            mode="staging-live",
            commit_sha="sha-test",
            image_repo_digest="sha256:abc123",
            session_factory=MagicMock(),
            search_port=MagicMock(),
            run_store=MagicMock(),
            hybrid_retrieve_request=MagicMock(),
            execution_fn=mock_exec_fn,
        )

    assert receipt.status == STATUS_SUCCESS
    assert receipt.execution_transaction_verified is True
    mock_exec_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_ret_h_smoke_real_composition_with_worker_transaction() -> None:
    from ai_worker.tasks.evaluation.ret_h_smoke import execute_ret_h_smoke_transaction
    from ai_worker.tasks.rag.retrieval_runtime import (
        HybridRetrieveOutcome,
        RetrievalExecutionStatus,
    )

    run_id = str(uuid4())
    receipt_hash = "e" * 64
    persisted_receipt_mock = MagicMock(run_id=run_id, receipt_hash=receipt_hash)
    fake_outcome = HybridRetrieveOutcome(
        status=RetrievalExecutionStatus.SUCCEEDED,
        persisted_receipt=persisted_receipt_mock,
        search_receipt=MagicMock(),
        gate_outcome=MagicMock(),
        message="Success",
    )

    with (
        patch(
            "ai_worker.tasks.evaluation.ret_h_smoke.execute_hybrid_retrieve",
            new=AsyncMock(return_value=fake_outcome),
        ) as mock_exec,
        patch("app.release_validation.ret_h_synthetic_smoke.check_aws_credentials_available", return_value=True),
        patch(
            "app.release_validation.ret_h_synthetic_smoke.run_verification_transaction",
            new=AsyncMock(return_value={"verified": True}),
        ),
    ):
        receipt = await run_ret_h_smoke(
            mode="staging-live",
            commit_sha="sha-test",
            image_repo_digest="sha256:abc123",
            session_factory=MagicMock(),
            search_port=MagicMock(),
            run_store=MagicMock(),
            hybrid_retrieve_request=MagicMock(),
            execution_fn=execute_ret_h_smoke_transaction,
        )

    assert receipt.status == STATUS_SUCCESS
    assert receipt.execution_transaction_verified is True
    mock_exec.assert_awaited_once()
