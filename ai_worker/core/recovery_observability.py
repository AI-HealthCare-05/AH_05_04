"""Pending 복구와 DLQ 처리의 안전한 운영 메트릭 경계입니다."""

import json
import logging
from typing import Literal, Protocol

type RecoveryTaskName = Literal[
    "pending_reconciler",
    "dlq_publisher",
]


class RecoveryMetrics(Protocol):
    """복구 경로가 기록할 수 있는 제한된 메트릭 계약입니다."""

    def record_reconciliation(
        self,
        *,
        expired_scanned: int,
        recovered_retry_wait: int,
        recovered_failed: int,
        retries_scheduled: int,
        reclaimed: int,
        not_recovered: int,
    ) -> None:
        """Pending reconciliation 한 cycle의 집계값을 기록합니다."""
        ...

    def record_dlq_publish(
        self,
        *,
        published: bool,
        retry_scheduled: bool,
        alert_required: bool,
    ) -> None:
        """DLQ 발행 한 cycle의 결과를 기록합니다."""
        ...

    def record_cycle_failure(
        self,
        *,
        task_name: RecoveryTaskName,
    ) -> None:
        """예외 원문 없이 실패한 복구 task만 기록합니다."""
        ...


class NoOpRecoveryMetrics:
    """관측성 Adapter가 연결되지 않은 테스트·개발 환경의 기본 구현입니다."""

    def record_reconciliation(
        self,
        *,
        expired_scanned: int,
        recovered_retry_wait: int,
        recovered_failed: int,
        retries_scheduled: int,
        reclaimed: int,
        not_recovered: int,
    ) -> None:
        _ = (
            expired_scanned,
            recovered_retry_wait,
            recovered_failed,
            retries_scheduled,
            reclaimed,
            not_recovered,
        )

    def record_dlq_publish(
        self,
        *,
        published: bool,
        retry_scheduled: bool,
        alert_required: bool,
    ) -> None:
        _ = published, retry_scheduled, alert_required

    def record_cycle_failure(
        self,
        *,
        task_name: RecoveryTaskName,
    ) -> None:
        _ = task_name


class RecoveryMetricLogger:
    """집계 가능한 JSON 로그로 복구 메트릭을 기록합니다."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def record_reconciliation(
        self,
        *,
        expired_scanned: int,
        recovered_retry_wait: int,
        recovered_failed: int,
        retries_scheduled: int,
        reclaimed: int,
        not_recovered: int,
    ) -> None:
        counts = {
            "expired_scanned": expired_scanned,
            "recovered_retry_wait": recovered_retry_wait,
            "recovered_failed": recovered_failed,
            "retries_scheduled": retries_scheduled,
            "reclaimed": reclaimed,
            "not_recovered": not_recovered,
        }
        _validate_counts(counts)

        self._emit(
            {
                "event": "worker_pending_reconciliation_completed",
                **counts,
            }
        )

    def record_dlq_publish(
        self,
        *,
        published: bool,
        retry_scheduled: bool,
        alert_required: bool,
    ) -> None:
        _validate_bool("published", published)
        _validate_bool("retry_scheduled", retry_scheduled)
        _validate_bool("alert_required", alert_required)

        self._emit(
            {
                "event": "worker_dlq_publish_completed",
                "published": published,
                "retry_scheduled": retry_scheduled,
                "alert_required": alert_required,
            }
        )

    def record_cycle_failure(
        self,
        *,
        task_name: RecoveryTaskName,
    ) -> None:
        if task_name not in {
            "pending_reconciler",
            "dlq_publisher",
        }:
            raise ValueError("승인되지 않은 recovery task_name입니다.")

        self._emit(
            {
                "event": "worker_recovery_cycle_failed",
                "task_name": task_name,
            }
        )

    def _emit(self, event: dict[str, object]) -> None:
        try:
            self._logger.info(
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        except Exception:
            # 관측성 실패가 reclaim·DLQ 처리 결과를 변경하지 않게 합니다.
            return


def _validate_counts(counts: dict[str, int]) -> None:
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name}은 정수여야 합니다.")
        if value < 0:
            raise ValueError(f"{name}은 0 이상이어야 합니다.")


def _validate_bool(name: str, value: bool) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name}은 bool이어야 합니다.")
