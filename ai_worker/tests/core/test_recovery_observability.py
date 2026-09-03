"""Pending 복구와 DLQ 운영 메트릭의 안전한 기록 계약 테스트입니다."""

import io
import json
import logging

from ai_worker.core.recovery_observability import RecoveryMetricLogger


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
