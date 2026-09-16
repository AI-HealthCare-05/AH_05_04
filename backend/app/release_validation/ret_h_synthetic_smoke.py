"""RET-H AWS synthetic deployment smoke verification.

This module owns the *fail-closed* orchestration of the Issue #178 RET-H synthetic
deployment smoke. It deliberately contains no ``ai_worker`` import: the production
retrieval callable, the canonical receipt verifier and the Evidence Gate negative
cases are injected by the caller (``scripts/ret_h_aws_synthetic_smoke.py``), which
preserves the PR #663 injection seam.

Fail-closed invariant
---------------------
A verification flag may only be ``True`` when the verification actually ran and
passed. A check that could not be performed never becomes a PASS: the run is
reported as ``FAILED`` (execution already happened) or ``AWS_SMOKE_NOT_EXECUTED``
(nothing ran).

The deployment under test is the repository's real one - a single AWS EC2 host
running ``infra/docker/docker-compose.prod.yml``. There is no ECS, CloudWatch or
SQS in this deployment and this module does not invent any.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, String, column, select, table

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "ret-h-aws-synthetic-smoke-v1"

AWS_SMOKE_NOT_EXECUTED = "AWS_SMOKE_NOT_EXECUTED"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"

DEPLOYMENT_KIND_EC2_DOCKER_COMPOSE = "EC2_DOCKER_COMPOSE"
WORKER_CONTAINER_NAME = "ai-worker"
FASTAPI_CONTAINER_NAME = "fastapi"
EXPECTED_WORKER_MEMORY_LIMIT_BYTES = 1073741824

EXPECTED_VARIANT = "RET-H"
EXPECTED_TERMINAL_STATUS = "COMPLETED"

EMBEDDING_MODEL_REF = "openai:text-embedding-3-large"
EMBEDDING_MODEL_VERSION = "text-embedding-3-large"
EMBEDDING_DIMENSION = 1536
EMBEDDING_CREDENTIAL_ENV_KEY = "OPENAI_API_KEY"

PUBLIC_TRACK_F_ENV_KEY = "PUBLIC_TRACK_F_ENABLED"
_FALSE_LITERALS = frozenset({"false", "0"})
_TRUE_LITERALS = frozenset({"true", "1"})

SENTINEL_PREFIX = "RET_H_SMOKE_"
SYNTHETIC_FIXTURE_ID = "RET_H_AWS_SYNTHETIC_SMOKE"

# Canonical blocking codes. BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL is the code already
# established by the Issue #178 completion design; the others follow its shape.
BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL = "BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL"
BLOCKED_BY_LOCAL_PREFLIGHT = "BLOCKED_BY_LOCAL_PREFLIGHT"
BLOCKED_BY_PUBLIC_TRACK_F_ENABLED = "BLOCKED_BY_PUBLIC_TRACK_F_ENABLED"
BLOCKED_BY_PUBLIC_TRACK_F_UNVERIFIED = "BLOCKED_BY_PUBLIC_TRACK_F_UNVERIFIED"
BLOCKED_BY_DEPLOYMENT_IDENTITY_UNVERIFIED = "BLOCKED_BY_DEPLOYMENT_IDENTITY_UNVERIFIED"
BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING = "BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING"

FAILED_BY_WORKER_MEMORY_LIMIT = "FAILED_BY_WORKER_MEMORY_LIMIT"
FAILED_BY_WORKER_OOM_KILLED = "FAILED_BY_WORKER_OOM_KILLED"
FAILED_BY_RETRIEVAL_EXECUTION = "FAILED_BY_RETRIEVAL_EXECUTION"
FAILED_BY_RUN_VERIFICATION = "FAILED_BY_RUN_VERIFICATION"
FAILED_BY_RECEIPT_MISMATCH = "FAILED_BY_RECEIPT_MISMATCH"
FAILED_BY_EVIDENCE_GATE_FAIL_OPEN = "FAILED_BY_EVIDENCE_GATE_FAIL_OPEN"
FAILED_BY_EVIDENCE_GATE_UNVERIFIED = "FAILED_BY_EVIDENCE_GATE_UNVERIFIED"
FAILED_BY_SENTINEL_FOUND = "FAILED_BY_SENTINEL_FOUND"
FAILED_BY_PRIVACY_SCAN_UNVERIFIED = "FAILED_BY_PRIVACY_SCAN_UNVERIFIED"
FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED = "FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED"

LEXICAL_SIGNAL_METHODS = frozenset({"EXACT", "TRIGRAM", "FTS", "LEXICAL"})
DENSE_SIGNAL_METHODS = frozenset({"DENSE"})

LIMITATIONS = (
    "synthetic deployment smoke only",
    "not a quality benchmark",
    "not a release approval",
    "not public activation",
    "RET-HR not executed",
)


class ScanState(StrEnum):
    """Privacy scan outcome for a single target.

    ``NOT_EXECUTED`` is never a pass: an unscanned target cannot be reported as
    ``SCANNED_AND_NOT_FOUND``.
    """

    SCANNED_AND_NOT_FOUND = "SCANNED_AND_NOT_FOUND"
    FOUND = "FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_EXECUTED = "NOT_EXECUTED"


# Targets that must actually be scanned before the smoke may report SUCCESS.
REQUIRED_SCAN_TARGETS = ("ai_worker_logs", "fastapi_logs", "redis_stream", "redis_dlq")


# --------------------------------------------------------------------------------------
# Sentinels
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SmokeSentinels:
    """Per-run unique synthetic sentinels.

    The raw values are used to build the synthetic query/Source and to scan logs.
    They are never serialised into the artifact - only their SHA-256 digests are.
    """

    query_sentinel: str
    source_sentinel: str

    @property
    def query_sentinel_sha256(self) -> str:
        return hashlib.sha256(self.query_sentinel.encode("utf-8")).hexdigest()

    @property
    def source_sentinel_sha256(self) -> str:
        return hashlib.sha256(self.source_sentinel.encode("utf-8")).hexdigest()

    def raw_values(self) -> tuple[str, str]:
        return (self.query_sentinel, self.source_sentinel)


def generate_sentinels(token_factory: Callable[[], str] = lambda: secrets.token_hex(16)) -> SmokeSentinels:
    return SmokeSentinels(
        query_sentinel=f"{SENTINEL_PREFIX}Q_{token_factory()}",
        source_sentinel=f"{SENTINEL_PREFIX}S_{token_factory()}",
    )


# --------------------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------------------


def evaluate_public_track_f(raw: str | None) -> bool | None:
    """Return the parsed flag, or ``None`` when the value cannot be verified.

    ``None`` is *not* an implicit false: an unreadable flag blocks the smoke.
    """
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _FALSE_LITERALS:
        return False
    if normalized in _TRUE_LITERALS:
        return True
    return None


def embedding_credential_present(environment: Mapping[str, str]) -> bool:
    """Report only whether an approved embedding credential is usable.

    The credential value is never returned, logged, hashed into the artifact or
    included in any exception raised from here.
    """
    raw = environment.get(EMBEDDING_CREDENTIAL_ENV_KEY)
    if raw is None:
        return False
    value = raw.strip()
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith(("fake-", "test-", "dummy-", "placeholder", "changeme", "<")):
        return False
    return True


@dataclass(frozen=True, slots=True)
class WorkerRuntimeFacts:
    """Allowlisted subset of ``docker inspect ai-worker``.

    The full container environment is deliberately never read or stored.
    """

    image: str
    image_id: str
    memory_limit_bytes: int
    restart_count: int
    oom_killed: bool
    state_status: str
    health_status: str | None


def parse_worker_inspect(payload: Any) -> WorkerRuntimeFacts:
    """Parse ``docker inspect ai-worker`` output, reading allowlisted fields only."""
    entries = payload if isinstance(payload, list) else [payload]
    if not entries or not isinstance(entries[0], dict):
        raise ValueError("docker inspect returned no container entry")
    entry: dict[str, Any] = entries[0]
    state = entry.get("State") or {}
    host_config = entry.get("HostConfig") or {}
    config = entry.get("Config") or {}
    health = state.get("Health") or {}

    memory_limit = host_config.get("Memory")
    if not isinstance(memory_limit, int):
        raise ValueError("docker inspect did not report HostConfig.Memory")

    return WorkerRuntimeFacts(
        image=str(config.get("Image") or entry.get("Image") or ""),
        image_id=str(entry.get("Image") or ""),
        memory_limit_bytes=memory_limit,
        restart_count=int(entry.get("RestartCount") or 0),
        oom_killed=bool(state.get("OOMKilled", False)),
        state_status=str(state.get("Status") or ""),
        health_status=str(health["Status"]) if health.get("Status") else None,
    )


def parse_image_digest(payload: Any) -> str | None:
    """Parse ``docker image inspect`` output for the first repository digest."""
    entries = payload if isinstance(payload, list) else [payload]
    if not entries or not isinstance(entries[0], dict):
        return None
    digests = entries[0].get("RepoDigests") or []
    if isinstance(digests, list) and digests:
        return str(digests[0])
    identifier = entries[0].get("Id")
    return str(identifier) if identifier else None


_MEM_UNITS = {
    "b": 1,
    "kb": 10**3,
    "mb": 10**6,
    "gb": 10**9,
    "tb": 10**12,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
_MEM_PATTERN = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([A-Za-z]+)\s*$")


def parse_memory_quantity(raw: str) -> int:
    """Convert a ``docker stats`` memory quantity such as ``742.4MiB`` to bytes."""
    match = _MEM_PATTERN.match(raw)
    if match is None:
        raise ValueError(f"Unparseable memory quantity: {raw!r}")
    amount, unit = match.groups()
    factor = _MEM_UNITS.get(unit.lower())
    if factor is None:
        raise ValueError(f"Unknown memory unit in {raw!r}")
    return int(float(amount) * factor)


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    memory_usage_bytes: int
    memory_limit_bytes: int
    cpu_percent: float | None


def parse_worker_stats(payload: Mapping[str, Any]) -> ResourceObservation:
    """Parse one ``docker stats --no-stream --format '{{json .}}'`` record."""
    usage_raw = str(payload.get("MemUsage") or "")
    if "/" not in usage_raw:
        raise ValueError("docker stats did not report MemUsage")
    used_text, limit_text = (part.strip() for part in usage_raw.split("/", 1))
    cpu_raw = str(payload.get("CPUPerc") or "").strip().rstrip("%")
    try:
        cpu_percent: float | None = float(cpu_raw)
    except ValueError:
        cpu_percent = None
    return ResourceObservation(
        memory_usage_bytes=parse_memory_quantity(used_text),
        memory_limit_bytes=parse_memory_quantity(limit_text),
        cpu_percent=cpu_percent,
    )


# --------------------------------------------------------------------------------------
# Evidence Gate negative cases
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateNegativeResult:
    """Outcome of one Evidence Gate fail-closed negative case.

    ``executed=False`` means the case never ran, which can never be a PASS.
    """

    executed: bool
    fail_closed: bool
    reason_code: str = ""
    message: str = ""

    @property
    def verified(self) -> bool:
        return self.executed and self.fail_closed


GateNegativeCallable = Callable[[], Awaitable[GateNegativeResult]]


async def _run_negative_case(case: GateNegativeCallable | None) -> GateNegativeResult:
    if case is None:
        return GateNegativeResult(executed=False, fail_closed=False, message="verifier not supplied")
    try:
        result = await case()
    except Exception as error:  # noqa: BLE001 - never leak provider/query detail
        return GateNegativeResult(executed=False, fail_closed=False, message=f"verifier error: {type(error).__name__}")
    if not isinstance(result, GateNegativeResult):
        return GateNegativeResult(executed=False, fail_closed=False, message="verifier returned an unusable result")
    return result


# --------------------------------------------------------------------------------------
# Privacy scan
# --------------------------------------------------------------------------------------

ScanReader = Callable[[], Awaitable[str] | str]


@dataclass(frozen=True, slots=True)
class ScanTarget:
    name: str
    reader: ScanReader | None = None
    applicable: bool = True


async def _read_scan_target(target: ScanTarget) -> str | None:
    if target.reader is None:
        return None
    try:
        raw = target.reader()
        if isinstance(raw, Awaitable):
            raw = await raw
    except Exception as error:  # noqa: BLE001 - never leak scanned content
        logger.warning("privacy scan target %s could not be read: %s", target.name, type(error).__name__)
        return None
    return raw if isinstance(raw, str) else None


async def scan_targets_for_sentinels(
    targets: Sequence[ScanTarget],
    sentinels: SmokeSentinels,
) -> dict[str, ScanState]:
    """Scan each target for the raw sentinels without recording any scanned content."""
    needles = [value for value in sentinels.raw_values() if value]
    results: dict[str, ScanState] = {}
    for target in targets:
        if not target.applicable:
            results[target.name] = ScanState.NOT_APPLICABLE
            continue
        content = await _read_scan_target(target)
        if content is None:
            results[target.name] = ScanState.NOT_EXECUTED
            continue
        results[target.name] = (
            ScanState.FOUND if any(needle in content for needle in needles) else ScanState.SCANNED_AND_NOT_FOUND
        )
    return results


def classify_privacy_scan(results: Mapping[str, ScanState]) -> tuple[bool, str | None]:
    """Return ``(verified, failure_code)`` for a completed privacy scan."""
    for name in REQUIRED_SCAN_TARGETS:
        state = results.get(name, ScanState.NOT_EXECUTED)
        if state is ScanState.FOUND:
            return False, FAILED_BY_SENTINEL_FOUND
        if state is not ScanState.SCANNED_AND_NOT_FOUND:
            return False, FAILED_BY_PRIVACY_SCAN_UNVERIFIED
    if any(state is ScanState.FOUND for state in results.values()):
        return False, FAILED_BY_SENTINEL_FOUND
    return True, None


# --------------------------------------------------------------------------------------
# Independent read-only verification transaction
# --------------------------------------------------------------------------------------

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
    """Independent read-only session asserting the persisted terminal Retrieval Run."""
    async with session_factory() as session:
        stmt_run = select(
            _RETRIEVAL_RUN.c.id,
            _RETRIEVAL_RUN.c.status,
            _RETRIEVAL_RUN.c.variant,
            _RETRIEVAL_RUN.c.receipt_hash,
        ).where(_RETRIEVAL_RUN.c.id == run_id)
        run_row = (await session.execute(stmt_run)).first()
        if run_row is None:
            raise AssertionError(f"retrieval_run row not found for run_id={run_id}")

        if run_row.status != EXPECTED_TERMINAL_STATUS:
            raise AssertionError(f"retrieval_run status expected {EXPECTED_TERMINAL_STATUS}, got {run_row.status}")

        if run_row.variant != EXPECTED_VARIANT:
            raise AssertionError(f"retrieval_run variant expected {EXPECTED_VARIANT}, got {run_row.variant}")

        if run_row.receipt_hash != expected_receipt_hash:
            raise AssertionError(f"retrieval_run receipt_hash mismatch for run_id={run_id}")

        stmt_signals = select(_RETRIEVAL_SIGNAL.c.retrieval_method).where(
            _RETRIEVAL_SIGNAL.c.retrieval_run_id == run_id
        )
        signals = (await session.execute(stmt_signals)).all()
        if not signals:
            raise AssertionError(f"No retrieval_signal rows found for run_id={run_id}")

        signal_methods = {str(s.retrieval_method).upper() for s in signals}
        if not signal_methods & LEXICAL_SIGNAL_METHODS:
            raise AssertionError(f"No lexical retrieval_signal found for run_id={run_id}")
        if not signal_methods & DENSE_SIGNAL_METHODS:
            raise AssertionError(f"No dense retrieval_signal found for run_id={run_id}")

        stmt_hits = select(_RETRIEVAL_HIT.c.selected).where(_RETRIEVAL_HIT.c.retrieval_run_id == run_id)
        hits = (await session.execute(stmt_hits)).all()
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
    """Delegate retrieval execution to the injected worker callable.

    Backend release validation never imports the ``ai_worker`` runtime directly.
    """
    if execution_fn is None:
        raise ValueError("Execution callable must be supplied by the caller (ai_worker).")
    return await execution_fn(
        request=request,
        search_port=search_port,
        text_embedding_port=text_embedding_port,
        run_store=run_store,
        eligibility_verifier=eligibility_verifier,
        **kwargs,
    )


# --------------------------------------------------------------------------------------
# Receipt
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetHSmokeReceipt:
    status: str
    mode: str
    started_at: str
    finished_at: str
    blocked_code: str | None = None
    error_message: str | None = None

    git_commit_sha: str | None = None
    deployment_kind: str = DEPLOYMENT_KIND_EC2_DOCKER_COMPOSE
    ai_worker_image: str | None = None
    ai_worker_image_id: str | None = None
    ai_worker_image_digest: str | None = None
    public_track_f_enabled: bool | None = None
    embedding_credential_present: bool = False

    fixture_id: str = SYNTHETIC_FIXTURE_ID
    knowledge_index_ref: str | None = None
    query_sentinel_sha256: str | None = None
    source_sentinel_sha256: str | None = None

    retrieval_run_id: str | None = None
    terminal_status: str | None = None
    receipt_verified: bool = False
    signal_count: int = 0
    hit_count: int = 0
    selected_hit_count: int = 0

    execution_transaction_verified: bool = False
    verification_transaction_verified: bool = False
    evidence_gate_positive_verified: bool = False
    evidence_gate_stale_fail_closed_verified: bool = False
    evidence_gate_locator_fail_closed_verified: bool = False

    privacy_scan: dict[str, str] = field(default_factory=dict)
    privacy_scan_verified: bool = False

    elapsed_ms: int | None = None
    worker_memory_usage_bytes: int | None = None
    worker_memory_limit_bytes: int | None = None
    worker_cpu_percent: float | None = None
    oom_killed: bool | None = None
    restart_count: int | None = None
    container_health: str | None = None
    resource_observation_verified: bool = False

    details: dict[str, Any] = field(default_factory=dict)

    def to_artifact(self) -> dict[str, Any]:
        """Render the sanitized artifact. Raw sentinels never reach this output."""
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "git_commit_sha": self.git_commit_sha,
            "blocked_code": self.blocked_code,
            "error_message": self.error_message,
            "deployment": {
                "kind": self.deployment_kind,
                "ai_worker_image": self.ai_worker_image,
                "ai_worker_image_id": self.ai_worker_image_id,
                "ai_worker_image_digest": self.ai_worker_image_digest,
                "public_track_f_enabled": self.public_track_f_enabled,
                "embedding_credential_present": self.embedding_credential_present,
            },
            "fixture": {
                "fixture_id": self.fixture_id,
                "knowledge_index_ref": self.knowledge_index_ref,
                "embedding_model": EMBEDDING_MODEL_REF,
                "embedding_model_version": EMBEDDING_MODEL_VERSION,
                "embedding_dimension": EMBEDDING_DIMENSION,
                "query_sentinel_sha256": self.query_sentinel_sha256,
                "source_sentinel_sha256": self.source_sentinel_sha256,
            },
            "retrieval": {
                "variant": EXPECTED_VARIANT,
                "retrieval_run_id": self.retrieval_run_id,
                "terminal_status": self.terminal_status,
                "execution_transaction_verified": self.execution_transaction_verified,
                "verification_transaction_verified": self.verification_transaction_verified,
                "receipt_verified": self.receipt_verified,
                "signal_count": self.signal_count,
                "hit_count": self.hit_count,
                "selected_hit_count": self.selected_hit_count,
                "reranker_calls": 0,
            },
            "evidence_gate": {
                "positive_verified": self.evidence_gate_positive_verified,
                "stale_fail_closed_verified": self.evidence_gate_stale_fail_closed_verified,
                "locator_mismatch_fail_closed_verified": self.evidence_gate_locator_fail_closed_verified,
            },
            "privacy": {
                "scan_verified": self.privacy_scan_verified,
                "targets": dict(self.privacy_scan),
            },
            "resources": {
                "elapsed_ms": self.elapsed_ms,
                "worker_memory_usage_bytes": self.worker_memory_usage_bytes,
                "worker_memory_limit_bytes": self.worker_memory_limit_bytes,
                "worker_cpu_percent": self.worker_cpu_percent,
                "oom_killed": self.oom_killed,
                "restart_count": self.restart_count,
                "container_health": self.container_health,
                "observation_verified": self.resource_observation_verified,
            },
            "limitations": list(LIMITATIONS),
            "details": dict(self.details),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _blocked(
    *,
    mode: str,
    started_at: str,
    code: str,
    message: str,
    **extra: Any,
) -> RetHSmokeReceipt:
    return RetHSmokeReceipt(
        status=AWS_SMOKE_NOT_EXECUTED,
        mode=mode,
        started_at=started_at,
        finished_at=_now(),
        blocked_code=code,
        error_message=message,
        **extra,
    )


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveSmokeDependencies:
    """Real production dependencies required for a live AWS RET-H smoke.

    Every field is mandatory for a ``SUCCESS``. A missing field blocks execution
    instead of silently degrading to a fake or an unverified PASS.
    """

    session_factory: Any = None
    verification_session_factory: Any = None
    search_port: Any = None
    text_embedding_port: Any = None
    run_store: Any = None
    eligibility_verifier: Any = None
    hybrid_retrieve_request: Any = None
    execution_fn: Any = None
    receipt_verifier: Callable[[Any], bool] | None = None
    stale_case: GateNegativeCallable | None = None
    locator_mismatch_case: GateNegativeCallable | None = None
    scan_targets: tuple[ScanTarget, ...] = ()
    knowledge_index_ref: str | None = None

    def missing(self) -> list[str]:
        required = (
            "session_factory",
            "verification_session_factory",
            "search_port",
            "text_embedding_port",
            "run_store",
            "eligibility_verifier",
            "hybrid_retrieve_request",
            "execution_fn",
            "receipt_verifier",
            "stale_case",
            "locator_mismatch_case",
        )
        absent = [name for name in required if getattr(self, name) is None]
        scanned = {target.name for target in self.scan_targets}
        absent.extend(f"scan_target:{name}" for name in REQUIRED_SCAN_TARGETS if name not in scanned)
        return absent


async def run_ret_h_smoke(  # noqa: C901, PLR0911, PLR0912, PLR0915
    *,
    mode: str,
    environment: Mapping[str, str],
    git_commit_sha: str | None = None,
    dependencies: LiveSmokeDependencies | None = None,
    worker_facts: WorkerRuntimeFacts | None = None,
    worker_image_digest: str | None = None,
    resource_observation: ResourceObservation | None = None,
    sentinels: SmokeSentinels | None = None,
    expected_memory_limit_bytes: int = EXPECTED_WORKER_MEMORY_LIMIT_BYTES,
) -> RetHSmokeReceipt:
    """Execute the RET-H AWS synthetic deployment smoke, fail-closed throughout."""
    started_at = _now()
    sentinels = sentinels or generate_sentinels()
    sentinel_fields: dict[str, Any] = {
        "query_sentinel_sha256": sentinels.query_sentinel_sha256,
        "source_sentinel_sha256": sentinels.source_sentinel_sha256,
        "git_commit_sha": git_commit_sha,
    }

    if mode != "aws-live":
        return _blocked(
            mode=mode,
            started_at=started_at,
            code=BLOCKED_BY_LOCAL_PREFLIGHT,
            message="Local preflight mode does not execute the AWS deployment smoke.",
            **sentinel_fields,
        )

    # 1. PUBLIC_TRACK_F must be provably false.
    public_track_f = evaluate_public_track_f(environment.get(PUBLIC_TRACK_F_ENV_KEY))
    if public_track_f is None:
        return _blocked(
            mode=mode,
            started_at=started_at,
            code=BLOCKED_BY_PUBLIC_TRACK_F_UNVERIFIED,
            message=f"{PUBLIC_TRACK_F_ENV_KEY} could not be verified as false.",
            **sentinel_fields,
        )
    if public_track_f is True:
        return _blocked(
            mode=mode,
            started_at=started_at,
            code=BLOCKED_BY_PUBLIC_TRACK_F_ENABLED,
            message=f"{PUBLIC_TRACK_F_ENV_KEY} is enabled; the RET-H smoke must not run.",
            public_track_f_enabled=True,
            **sentinel_fields,
        )
    sentinel_fields["public_track_f_enabled"] = False

    # 2. Approved embedding credential must exist. No fake-embedding fallback.
    credential_present = embedding_credential_present(environment)
    if not credential_present:
        return _blocked(
            mode=mode,
            started_at=started_at,
            code=BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL,
            message="Approved OpenAI embedding credential is unavailable; RET-H requires a real dense query embedding.",
            **sentinel_fields,
        )
    sentinel_fields["embedding_credential_present"] = True

    # 3. Deployment identity must be observed from the running container.
    if worker_facts is None:
        return _blocked(
            mode=mode,
            started_at=started_at,
            code=BLOCKED_BY_DEPLOYMENT_IDENTITY_UNVERIFIED,
            message="Running ai-worker container identity could not be observed.",
            **sentinel_fields,
        )
    sentinel_fields.update(
        ai_worker_image=worker_facts.image,
        ai_worker_image_id=worker_facts.image_id,
        ai_worker_image_digest=worker_image_digest,
    )
    deployment_fields = dict(sentinel_fields)
    deployment_fields.update(
        oom_killed=worker_facts.oom_killed,
        restart_count=worker_facts.restart_count,
        container_health=worker_facts.health_status,
        worker_memory_limit_bytes=worker_facts.memory_limit_bytes,
    )

    def _failed(code: str, message: str, **extra: Any) -> RetHSmokeReceipt:
        merged = dict(deployment_fields)
        merged.update(extra)
        return RetHSmokeReceipt(
            status=STATUS_FAILED,
            mode=mode,
            started_at=started_at,
            finished_at=_now(),
            blocked_code=code,
            error_message=message,
            **merged,
        )

    # 4. Deployed resource envelope must match the approved 1 GiB worker budget.
    if worker_facts.memory_limit_bytes != expected_memory_limit_bytes:
        return _failed(
            FAILED_BY_WORKER_MEMORY_LIMIT,
            f"ai-worker memory limit {worker_facts.memory_limit_bytes} != {expected_memory_limit_bytes}",
        )
    if worker_facts.oom_killed:
        return _failed(FAILED_BY_WORKER_OOM_KILLED, "ai-worker container reports OOMKilled=true")

    # 5. All production dependencies must be present before anything executes.
    dependencies = dependencies or LiveSmokeDependencies()
    missing = dependencies.missing()
    if missing:
        return _blocked(
            mode=mode,
            started_at=started_at,
            code=BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING,
            message=f"Production smoke dependencies not assembled: {', '.join(sorted(missing))}",
            **sentinel_fields,
        )
    deployment_fields["knowledge_index_ref"] = dependencies.knowledge_index_ref

    # 6. Real production RET-H execution.
    start_monotonic = datetime.now(UTC)
    try:
        outcome = await run_execution_transaction(
            execution_fn=dependencies.execution_fn,
            request=dependencies.hybrid_retrieve_request,
            search_port=dependencies.search_port,
            text_embedding_port=dependencies.text_embedding_port,
            run_store=dependencies.run_store,
            eligibility_verifier=dependencies.eligibility_verifier,
        )
    except Exception as error:  # noqa: BLE001 - never leak query/Source text
        return _failed(FAILED_BY_RETRIEVAL_EXECUTION, f"execute_hybrid_retrieve raised {type(error).__name__}")
    elapsed_ms = int((datetime.now(UTC) - start_monotonic).total_seconds() * 1000)
    deployment_fields["elapsed_ms"] = elapsed_ms

    status_val = getattr(outcome, "status", None)
    status_str = str(getattr(status_val, "name", status_val))
    persisted_receipt = getattr(outcome, "persisted_receipt", None)
    if status_str != "SUCCEEDED" or persisted_receipt is None:
        return _failed(FAILED_BY_RETRIEVAL_EXECUTION, f"execute_hybrid_retrieve terminal status was {status_str}")

    run_id = str(persisted_receipt.run_id)
    expected_receipt_hash = str(persisted_receipt.receipt_hash)
    deployment_fields.update(retrieval_run_id=run_id, execution_transaction_verified=True)

    gate_outcome = getattr(outcome, "gate_outcome", None)
    positive_selected = tuple(getattr(gate_outcome, "selected_hits", ()) or ())
    if not positive_selected:
        return _failed(
            FAILED_BY_EVIDENCE_GATE_FAIL_OPEN,
            "Evidence Gate returned no selected hit for the synthetic positive query",
        )
    deployment_fields["evidence_gate_positive_verified"] = True

    # 7. Independent read-only verification session.
    try:
        details = await run_verification_transaction(
            session_factory=dependencies.verification_session_factory,
            run_id=run_id,
            expected_receipt_hash=expected_receipt_hash,
        )
    except Exception as error:  # noqa: BLE001
        return _failed(FAILED_BY_RUN_VERIFICATION, f"Verification transaction failed: {error}")

    deployment_fields.update(
        verification_transaction_verified=True,
        terminal_status=str(details.get("status")),
        signal_count=int(details.get("signals_count", 0)),
        hit_count=int(details.get("hits_count", 0)),
        selected_hit_count=int(details.get("selected_hits_count", 0)),
        details=details,
    )

    # 8. Canonical receipt verification via the injected production verifier.
    try:
        receipt_ok = bool(dependencies.receipt_verifier(persisted_receipt))  # type: ignore[misc]
    except Exception as error:  # noqa: BLE001
        return _failed(FAILED_BY_RECEIPT_MISMATCH, f"Receipt verification raised {type(error).__name__}")
    if not receipt_ok:
        return _failed(FAILED_BY_RECEIPT_MISMATCH, "Canonical receipt hash verification failed")
    deployment_fields["receipt_verified"] = True

    # 9. Evidence Gate negative cases - both must actually run and fail closed.
    stale_result = await _run_negative_case(dependencies.stale_case)
    locator_result = await _run_negative_case(dependencies.locator_mismatch_case)
    deployment_fields.update(
        evidence_gate_stale_fail_closed_verified=stale_result.verified,
        evidence_gate_locator_fail_closed_verified=locator_result.verified,
    )
    for label, result in (("stale", stale_result), ("locator mismatch", locator_result)):
        if not result.executed:
            return _failed(
                FAILED_BY_EVIDENCE_GATE_UNVERIFIED,
                f"Evidence Gate {label} negative case did not execute: {result.message}",
            )
        if not result.fail_closed:
            return _failed(
                FAILED_BY_EVIDENCE_GATE_FAIL_OPEN,
                f"Evidence Gate {label} negative case was not fail-closed",
            )

    # 10. Raw query/Source sentinels must be provably absent.
    scan_results = await scan_targets_for_sentinels(dependencies.scan_targets, sentinels)
    deployment_fields["privacy_scan"] = {name: str(state) for name, state in scan_results.items()}
    privacy_ok, privacy_code = classify_privacy_scan(scan_results)
    deployment_fields["privacy_scan_verified"] = privacy_ok
    if not privacy_ok:
        return _failed(privacy_code or FAILED_BY_PRIVACY_SCAN_UNVERIFIED, "Privacy sentinel scan did not pass")

    # 11. Resource observation.
    if resource_observation is None:
        return _failed(FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED, "ai-worker resource observation was not collected")
    deployment_fields.update(
        worker_memory_usage_bytes=resource_observation.memory_usage_bytes,
        worker_cpu_percent=resource_observation.cpu_percent,
        resource_observation_verified=True,
    )

    return RetHSmokeReceipt(
        status=STATUS_SUCCESS,
        mode=mode,
        started_at=started_at,
        finished_at=_now(),
        **deployment_fields,
    )


# --------------------------------------------------------------------------------------
# Docker observation helpers (EC2 + Docker Compose)
# --------------------------------------------------------------------------------------

CommandRunner = Callable[[Sequence[str]], str]


def default_command_runner(args: Sequence[str]) -> str:
    executable = shutil.which(args[0])
    if executable is None:
        raise FileNotFoundError(f"{args[0]} is not available on this host")
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [executable, *args[1:]],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout


def collect_worker_facts(
    *,
    runner: CommandRunner = default_command_runner,
    container: str = WORKER_CONTAINER_NAME,
) -> tuple[WorkerRuntimeFacts, str | None]:
    """Observe the running worker container and its image digest."""
    facts = parse_worker_inspect(json.loads(runner(["docker", "inspect", container])))
    digest: str | None = None
    if facts.image_id:
        try:
            digest = parse_image_digest(json.loads(runner(["docker", "image", "inspect", facts.image_id])))
        except Exception as error:  # noqa: BLE001
            logger.warning("image digest could not be observed: %s", type(error).__name__)
    return facts, digest


def collect_resource_observation(
    *,
    runner: CommandRunner = default_command_runner,
    container: str = WORKER_CONTAINER_NAME,
) -> ResourceObservation:
    raw = runner(["docker", "stats", "--no-stream", "--format", "{{json .}}", container])
    line = next((entry for entry in raw.splitlines() if entry.strip()), "")
    return parse_worker_stats(json.loads(line))


def build_docker_log_reader(
    container: str,
    *,
    runner: CommandRunner = default_command_runner,
    since: str | None = None,
) -> ScanReader:
    def _read() -> str:
        args = ["docker", "logs", "--no-color"]
        if since:
            args.extend(["--since", since])
        args.append(container)
        return runner(args)

    return _read


def build_redis_stream_reader(
    stream: str,
    *,
    runner: CommandRunner = default_command_runner,
    container: str = "redis",
    count: int = 500,
) -> ScanReader:
    """Read the tail of a Redis stream non-destructively.

    ``REDISCLI_AUTH`` is taken from the container environment by ``sh -c`` so the
    password never appears in this process's argv or in any captured output.
    """

    def _read() -> str:
        script = f'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --no-auth-warning XREVRANGE {stream} + - COUNT {count}'
        return runner(["docker", "exec", container, "sh", "-c", script])

    return _read


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RET-H AWS synthetic deployment smoke runner.")
    parser.add_argument(
        "--mode",
        choices=["aws-live", "local-preflight"],
        default="local-preflight",
        help="aws-live executes the real deployment smoke; local-preflight never does.",
    )
    parser.add_argument("--git-commit-sha", default=None, help="Deployed git commit SHA.")
    parser.add_argument("--output-path", type=Path, default=None, help="Sanitized artifact output path.")
    return parser


def write_artifact(receipt: RetHSmokeReceipt, output_path: Path | None) -> str:
    payload = json.dumps(receipt.to_artifact(), indent=2, ensure_ascii=False, sort_keys=True)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Preflight-only entry point.

    The live AWS composition root is ``scripts/ret_h_aws_synthetic_smoke.py``; this
    entry point never assembles production dependencies, so it can only ever report
    ``AWS_SMOKE_NOT_EXECUTED``.
    """
    args = build_parser().parse_args(argv)
    receipt = asyncio.run(
        run_ret_h_smoke(
            mode="local-preflight" if args.mode != "aws-live" else args.mode,
            environment=os.environ,
            git_commit_sha=args.git_commit_sha,
        )
    )
    payload = write_artifact(receipt, args.output_path)
    if args.output_path is None:
        print(payload)
    return 1 if receipt.status == STATUS_FAILED else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
