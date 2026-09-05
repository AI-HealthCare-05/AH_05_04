"""Pending 복구와 DLQ 운영 메트릭의 안전한 기록 계약 테스트입니다."""

import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from ai_worker.core.dlq import DlqPublishReport
from ai_worker.core.reconciler import ReconciliationReport
from ai_worker.core.recovery_observability import (
    ObservedDlqPublisher,
    ObservedPendingReconciler,
    RecoveryMetricLogger,
)


def build_logger(stream: io.StringIO) -> logging.Logger:
    logger = logging.Logger(
        "worker-recovery-metrics-test",
        level=logging.INFO,
    )
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def read_event(stream: io.StringIO) -> dict[str, object]:
    return json.loads(stream.getvalue())


def test_reconciliation_metrics_include_only_operational_counts() -> None:
    stream = io.StringIO()
    recorder = RecoveryMetricLogger(build_logger(stream))

    recorder.record_reconciliation(
        expired_scanned=4,
        recovered_retry_wait=2,
        recovered_failed=1,
        retries_scheduled=3,
        reclaimed=2,
        not_recovered=1,
    )

    assert read_event(stream) == {
        "event": "worker_pending_reconciliation_completed",
        "expired_scanned": 4,
        "recovered_retry_wait": 2,
        "recovered_failed": 1,
        "retries_scheduled": 3,
        "reclaimed": 2,
        "not_recovered": 1,
    }


def test_dlq_publish_metrics_record_retry_and_alert_state() -> None:
    stream = io.StringIO()
    recorder = RecoveryMetricLogger(build_logger(stream))

    recorder.record_dlq_publish(
        published=False,
        retry_scheduled=True,
        alert_required=True,
    )

    assert read_event(stream) == {
        "event": "worker_dlq_publish_completed",
        "published": False,
        "retry_scheduled": True,
        "alert_required": True,
    }


def test_cycle_failure_does_not_accept_exception_or_message_content() -> None:
    stream = io.StringIO()
    recorder = RecoveryMetricLogger(build_logger(stream))

    recorder.record_cycle_failure(
        task_name="pending_reconciler",
    )

    assert read_event(stream) == {
        "event": "worker_recovery_cycle_failed",
        "task_name": "pending_reconciler",
    }


@pytest.mark.asyncio
async def test_observed_reconciler_records_completed_report() -> None:
    report = ReconciliationReport(
        expired_scanned=4,
        recovered_retry_wait=2,
        recovered_failed=1,
        retries_scheduled=3,
        reclaimed=2,
        not_recovered=1,
        next_start_id="10-0",
    )
    task = SimpleNamespace(
        run_once=AsyncMock(return_value=report),
    )
    metrics = SimpleNamespace(
        record_reconciliation=Mock(),
    )
    observed = ObservedPendingReconciler(
        task=task,
        metrics=metrics,
    )

    result = await observed.run_once()

    assert result == report
    metrics.record_reconciliation.assert_called_once_with(
        expired_scanned=4,
        recovered_retry_wait=2,
        recovered_failed=1,
        retries_scheduled=3,
        reclaimed=2,
        not_recovered=1,
    )


@pytest.mark.asyncio
async def test_observed_dlq_publisher_records_completed_report() -> None:
    report = DlqPublishReport(
        event_id=uuid4(),
        stream_message_id=None,
        published=False,
        retry_scheduled=True,
        alert_required=True,
    )
    task = SimpleNamespace(
        run_once=AsyncMock(return_value=report),
    )
    metrics = SimpleNamespace(
        record_dlq_publish=Mock(),
    )
    observed = ObservedDlqPublisher(
        task=task,
        metrics=metrics,
    )

    result = await observed.run_once()

    assert result == report
    metrics.record_dlq_publish.assert_called_once_with(
        published=False,
        retry_scheduled=True,
        alert_required=True,
    )


@pytest.mark.asyncio
async def test_recovery_logger_reports_scheduler_failure_safely() -> None:
    stream = io.StringIO()
    reporter = RecoveryMetricLogger(build_logger(stream))

    await reporter.report_failure(
        task_name="pending_reconciler",
    )

    assert read_event(stream) == {
        "event": "worker_recovery_cycle_failed",
        "task_name": "pending_reconciler",
    }


@pytest.mark.asyncio
async def test_recovery_logger_emits_dlq_alert_without_payload() -> None:
    stream = io.StringIO()
    reporter = RecoveryMetricLogger(build_logger(stream))
    event_id = uuid4()

    await reporter.notify_publish_failure(
        event_id=event_id,
        attempt_count=10,
    )

    assert read_event(stream) == {
        "event": "worker_dlq_publish_alert",
        "event_id": str(event_id),
        "attempt_count": 10,
    }
