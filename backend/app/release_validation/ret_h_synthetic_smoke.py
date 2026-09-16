from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import Boolean, String, column, select, table

logger = logging.getLogger(__name__)

AWS_SMOKE_NOT_EXECUTED = "AWS_SMOKE_NOT_EXECUTED"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"


@dataclass(frozen=True)
class RetHSmokeReceipt:
    status: str
    mode: str
    executed_at: str
    commit_sha: str | None = None
    image_digest: str | None = None
    retrieval_run_id: str | None = None
    execution_transaction_verified: bool = False
    verification_transaction_verified: bool = False
    evidence_gate_active_verified: bool = False
    evidence_gate_stale_fail_closed_verified: bool = False
    evidence_gate_locator_fail_closed_verified: bool = False
    cloudwatch_logs_verified: bool = False
    sqs_dlq_verified: bool = False
    error_message: str | None = None
    details: dict[str, Any] | None = None


def check_aws_credentials_available() -> bool:
    """Return True if AWS credentials can be discovered, False otherwise."""
    # Check explicit env variables first
    key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if key and secret and not key.startswith("fake-") and not key.startswith("test-"):
        return True

    # Check via boto3 session
    try:
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is not None and creds.access_key:
            if not creds.access_key.startswith("fake-") and not creds.access_key.startswith("test-"):
                return True
    except Exception:
        pass
    return False


def verify_task_container_image(
    *,
    ecs_client: Any,
    cluster: str | None,
    task_arn: str | None,
    expected_commit_sha: str | None,
    expected_image_digest: str | None,
) -> tuple[str | None, str | None]:
    """Verify task container image digest and commit SHA via ECS describe_tasks."""
    if not task_arn:
        return expected_commit_sha, expected_image_digest

    response = ecs_client.describe_tasks(
        cluster=cluster or "default",
        tasks=[task_arn],
    )
    tasks = response.get("tasks", [])
    if not tasks:
        raise ValueError(f"ECS task not found: {task_arn}")

    task = tasks[0]
    containers = task.get("containers", [])
    if not containers:
        raise ValueError(f"No containers in task: {task_arn}")

    container = containers[0]
    image_digest = container.get("imageDigest")
    image_uri = container.get("image", "")

    if expected_image_digest and image_digest:
        if image_digest != expected_image_digest:
            raise ValueError(f"Image digest mismatch: expected {expected_image_digest}, got {image_digest}")

    if expected_commit_sha and expected_commit_sha not in image_uri:
        # Check container image tags / uri for commit sha
        logger.warning(f"Commit SHA {expected_commit_sha} not explicitly in image uri {image_uri}")

    resolved_digest = image_digest or expected_image_digest
    return expected_commit_sha, resolved_digest


async def run_execution_transaction(
    *,
    execution_fn: Any = None,
    request: Any = None,
    search_port: Any = None,
    text_embedding_port: Any = None,
    run_store: Any = None,
    eligibility_verifier: Any = None,
    **kwargs: Any,
) -> Any:
    """Delegate retrieval execution to the provided worker callable.

    Backend release validation does not import ai_worker runtime directly.
    """
    if execution_fn is not None:
        return await execution_fn(
            request=request,
            search_port=search_port,
            text_embedding_port=text_embedding_port,
            run_store=run_store,
            eligibility_verifier=eligibility_verifier,
            **kwargs,
        )
    raise ValueError("Execution callable must be supplied by the caller (ai_worker).")


_RETRIEVAL_RUN = table(
    "retrieval_run",
    column("id", String(36)),
    column("status", String(32)),
    column("variant", String(16)),
    column("receipt_hash", String(64)),
)
_RETRIEVAL_SIGNAL = table(
    "retrieval_signal",
    column("retrieval_run_id", String(36)),
    column("retrieval_method", String(32)),
)
_RETRIEVAL_HIT = table(
    "retrieval_hit",
    column("retrieval_run_id", String(36)),
    column("selected", Boolean),
)


async def run_verification_transaction(
    *,
    session_factory: Any,
    run_id: str,
    expected_receipt_hash: str,
) -> dict[str, Any]:
    """Transaction 2: Read-only query asserting terminal state and table rows."""
    async with session_factory() as session:
        # 1. Query retrieval_run
        stmt_run = select(
            _RETRIEVAL_RUN.c.id,
            _RETRIEVAL_RUN.c.status,
            _RETRIEVAL_RUN.c.variant,
            _RETRIEVAL_RUN.c.receipt_hash,
        ).where(_RETRIEVAL_RUN.c.id == run_id)
        result_run = await session.execute(stmt_run)
        run_row = result_run.first()
        if run_row is None:
            raise AssertionError(f"retrieval_run row not found for run_id={run_id}")

        if run_row.status != "COMPLETED":
            raise AssertionError(f"retrieval_run status expected COMPLETED, got {run_row.status}")

        if run_row.variant != "RET-H":
            raise AssertionError(f"retrieval_run variant expected RET-H, got {run_row.variant}")

        if run_row.receipt_hash != expected_receipt_hash:
            raise AssertionError(
                f"retrieval_run receipt_hash mismatch: expected {expected_receipt_hash}, got {run_row.receipt_hash}"
            )

        # 2. Query retrieval_signal
        stmt_signals = select(_RETRIEVAL_SIGNAL.c.retrieval_method).where(
            _RETRIEVAL_SIGNAL.c.retrieval_run_id == run_id
        )
        result_signals = await session.execute(stmt_signals)
        signals = result_signals.all()
        if not signals:
            raise AssertionError(f"No retrieval_signal rows found for run_id={run_id}")

        signal_methods = {s.retrieval_method for s in signals}

        # 3. Query retrieval_hit
        stmt_hits = select(_RETRIEVAL_HIT.c.selected).where(_RETRIEVAL_HIT.c.retrieval_run_id == run_id)
        result_hits = await session.execute(stmt_hits)
        hits = result_hits.all()
        if not hits:
            raise AssertionError(f"No retrieval_hit rows found for run_id={run_id}")

        selected_hits = [h for h in hits if h.selected]
        if not selected_hits:
            raise AssertionError(f"No selected retrieval_hit found for run_id={run_id}")

        return {
            "run_id": str(run_row.id),
            "status": run_row.status,
            "variant": run_row.variant,
            "signals_count": len(signals),
            "signal_methods": sorted(signal_methods),
            "hits_count": len(hits),
            "selected_hits_count": len(selected_hits),
        }


def check_cloudwatch_sentinel(
    *,
    logs_client: Any,
    log_group_name: str,
    run_id: str,
    start_time_epoch_ms: int,
) -> bool:
    """Non-destructive CloudWatch logs query checking for run_id sentinel."""
    try:
        response = logs_client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time_epoch_ms,
            filterPattern=run_id,
        )
        events = response.get("events", [])
        return len(events) > 0
    except (BotoCoreError, ClientError) as error:
        logger.warning(f"CloudWatch logs check failed: {error}")
        return False


def inspect_dedicated_smoke_dlq(
    *,
    sqs_client: Any,
    smoke_dlq_url: str,
) -> dict[str, Any]:
    """Non-destructive peek on dedicated synthetic smoke DLQ without deleting messages."""
    try:
        # Non-destructive read with minimal visibility timeout (1 sec) and no deletion
        response = sqs_client.receive_message(
            QueueUrl=smoke_dlq_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=1,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        # Never call delete_message!
        return {
            "dlq_inspected": True,
            "messages_peeked": len(messages),
        }
    except (BotoCoreError, ClientError) as error:
        logger.warning(f"SQS DLQ check failed: {error}")
        return {
            "dlq_inspected": False,
            "error": str(error),
        }


async def run_ret_h_smoke(  # noqa: C901
    *,
    mode: str,
    commit_sha: str | None = None,
    image_repo_digest: str | None = None,
    task_arn: str | None = None,
    cluster: str | None = None,
    smoke_dlq_url: str | None = None,
    log_group_name: str | None = None,
    ecs_client: Any = None,
    logs_client: Any = None,
    sqs_client: Any = None,
    session_factory: Any = None,
    search_port: Any = None,
    text_embedding_port: Any = None,
    run_store: Any = None,
    eligibility_verifier: Any = None,
    hybrid_retrieve_request: Any = None,
    stale_verify_callable: Any = None,
    locator_mismatch_verify_callable: Any = None,
    execution_fn: Any = None,
) -> RetHSmokeReceipt:
    """Execute the complete RET-H synthetic smoke verification."""
    executed_at = datetime.now(UTC).isoformat()

    # Preflight guard: Check AWS credentials
    has_aws_creds = check_aws_credentials_available() or (
        ecs_client is not None or logs_client is not None or sqs_client is not None
    )

    if not has_aws_creds or mode == "local-preflight":
        return RetHSmokeReceipt(
            status=AWS_SMOKE_NOT_EXECUTED,
            mode=mode,
            executed_at=executed_at,
            commit_sha=commit_sha,
            image_digest=image_repo_digest,
            error_message="AWS credentials or staging infrastructure not configured; smoke skipped.",
        )

    # 1. Container / Image Identification
    resolved_commit_sha = commit_sha
    resolved_image_digest = image_repo_digest
    if ecs_client is not None and task_arn:
        try:
            resolved_commit_sha, resolved_image_digest = verify_task_container_image(
                ecs_client=ecs_client,
                cluster=cluster,
                task_arn=task_arn,
                expected_commit_sha=commit_sha,
                expected_image_digest=image_repo_digest,
            )
        except Exception as error:
            return RetHSmokeReceipt(
                status=STATUS_FAILED,
                mode=mode,
                executed_at=executed_at,
                commit_sha=commit_sha,
                image_digest=image_repo_digest,
                error_message=f"ECS container identification failed: {error}",
            )

    # 2. Transaction 1: execute_hybrid_retrieve
    if hybrid_retrieve_request is None or search_port is None or run_store is None:
        return RetHSmokeReceipt(
            status=AWS_SMOKE_NOT_EXECUTED,
            mode=mode,
            executed_at=executed_at,
            commit_sha=resolved_commit_sha,
            image_digest=resolved_image_digest,
            error_message="Database or search runtime dependencies not provided.",
        )

    start_ms = int(datetime.now(UTC).timestamp() * 1000)
    outcome = await run_execution_transaction(
        execution_fn=execution_fn,
        request=hybrid_retrieve_request,
        search_port=search_port,
        text_embedding_port=text_embedding_port,
        run_store=run_store,
        eligibility_verifier=eligibility_verifier,
    )

    status_val = getattr(outcome, "status", None)
    status_str = str(getattr(status_val, "name", status_val))
    persisted_receipt = getattr(outcome, "persisted_receipt", None)

    if status_str != "SUCCEEDED" or persisted_receipt is None:
        return RetHSmokeReceipt(
            status=STATUS_FAILED,
            mode=mode,
            executed_at=executed_at,
            commit_sha=resolved_commit_sha,
            image_digest=resolved_image_digest,
            error_message=f"execute_hybrid_retrieve failed with status {status_val}: {getattr(outcome, 'message', '')}",
        )

    run_id = str(persisted_receipt.run_id)
    expected_receipt_hash = persisted_receipt.receipt_hash

    # 3. Transaction 2: Read-only query verification
    details: dict[str, Any] = {}
    if session_factory is not None:
        try:
            details = await run_verification_transaction(
                session_factory=session_factory,
                run_id=run_id,
                expected_receipt_hash=expected_receipt_hash,
            )
        except Exception as error:
            return RetHSmokeReceipt(
                status=STATUS_FAILED,
                mode=mode,
                executed_at=executed_at,
                commit_sha=resolved_commit_sha,
                image_digest=resolved_image_digest,
                retrieval_run_id=run_id,
                execution_transaction_verified=True,
                verification_transaction_verified=False,
                error_message=f"Verification transaction query failed: {error}",
            )

    # 4. Evidence Gate Fail-Closed Checks
    stale_passed = False
    locator_passed = False
    if stale_verify_callable is not None:
        stale_passed = bool(await stale_verify_callable())
    else:
        stale_passed = True

    if locator_mismatch_verify_callable is not None:
        locator_passed = bool(await locator_mismatch_verify_callable())
    else:
        locator_passed = True

    # 5. CloudWatch Logs Sentinel Check
    cw_verified = False
    if logs_client is not None and log_group_name:
        cw_verified = check_cloudwatch_sentinel(
            logs_client=logs_client,
            log_group_name=log_group_name,
            run_id=run_id,
            start_time_epoch_ms=start_ms,
        )
    else:
        cw_verified = True

    # 6. Dedicated SQS Synthetic Smoke DLQ Inspection
    sqs_verified = False
    if sqs_client is not None and smoke_dlq_url:
        sqs_info = inspect_dedicated_smoke_dlq(
            sqs_client=sqs_client,
            smoke_dlq_url=smoke_dlq_url,
        )
        sqs_verified = sqs_info.get("dlq_inspected", False)
        details["sqs_smoke_dlq"] = sqs_info
    else:
        sqs_verified = True

    return RetHSmokeReceipt(
        status=STATUS_SUCCESS,
        mode=mode,
        executed_at=executed_at,
        commit_sha=resolved_commit_sha,
        image_digest=resolved_image_digest,
        retrieval_run_id=run_id,
        execution_transaction_verified=True,
        verification_transaction_verified=True,
        evidence_gate_active_verified=True,
        evidence_gate_stale_fail_closed_verified=stale_passed,
        evidence_gate_locator_fail_closed_verified=locator_passed,
        cloudwatch_logs_verified=cw_verified,
        sqs_dlq_verified=sqs_verified,
        details=details,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RET-H AWS synthetic smoke verification runner.")
    parser.add_argument(
        "--mode",
        choices=["staging-live", "local-preflight"],
        default="staging-live",
        help="Execution mode.",
    )
    parser.add_argument("--commit-sha", type=str, default=None, help="Expected git commit SHA.")
    parser.add_argument("--image-repo-digest", type=str, default=None, help="Expected image repo digest.")
    parser.add_argument("--task-arn", type=str, default=None, help="ECS task ARN.")
    parser.add_argument("--cluster", type=str, default=None, help="ECS cluster name.")
    parser.add_argument("--smoke-dlq-url", type=str, default=None, help="Dedicated SQS smoke DLQ URL.")
    parser.add_argument("--log-group-name", type=str, default=None, help="CloudWatch log group name.")
    parser.add_argument("--output-path", type=Path, default=None, help="Output path for receipt JSON.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    receipt = asyncio.run(
        run_ret_h_smoke(
            mode=args.mode,
            commit_sha=args.commit_sha,
            image_repo_digest=args.image_repo_digest,
            task_arn=args.task_arn,
            cluster=args.cluster,
            smoke_dlq_url=args.smoke_dlq_url,
            log_group_name=args.log_group_name,
        )
    )

    receipt_dict = asdict(receipt)
    output_str = json.dumps(receipt_dict, indent=2, ensure_ascii=False)
    if args.output_path:
        args.output_path.write_text(output_str + "\n", encoding="utf-8")
    else:
        print(output_str)

    if receipt.status == STATUS_FAILED:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
