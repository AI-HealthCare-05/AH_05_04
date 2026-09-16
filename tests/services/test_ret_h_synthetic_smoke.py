"""Fail-closed regression tests for the Issue #178 RET-H AWS synthetic smoke.

Every test here exists to pin one rule: a verification that did not actually run
and pass can never be reported as a PASS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.release_validation.ret_h_synthetic_smoke import (
    AWS_SMOKE_NOT_EXECUTED,
    BLOCKED_BY_DEPLOYMENT_IDENTITY_UNVERIFIED,
    BLOCKED_BY_LOCAL_PREFLIGHT,
    BLOCKED_BY_PUBLIC_TRACK_F_ENABLED,
    BLOCKED_BY_PUBLIC_TRACK_F_UNVERIFIED,
    BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL,
    BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING,
    EXPECTED_WORKER_MEMORY_LIMIT_BYTES,
    FAILED_BY_EVIDENCE_GATE_FAIL_OPEN,
    FAILED_BY_EVIDENCE_GATE_UNVERIFIED,
    FAILED_BY_PRIVACY_SCAN_UNVERIFIED,
    FAILED_BY_RECEIPT_MISMATCH,
    FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED,
    FAILED_BY_RUN_VERIFICATION,
    FAILED_BY_SENTINEL_FOUND,
    FAILED_BY_WORKER_MEMORY_LIMIT,
    FAILED_BY_WORKER_OOM_KILLED,
    SCHEMA_VERSION,
    STATUS_FAILED,
    STATUS_SUCCESS,
    GateNegativeResult,
    LiveSmokeDependencies,
    ScanState,
    ScanTarget,
    SmokeSentinels,
    WorkerRuntimeFacts,
    classify_privacy_scan,
    embedding_credential_present,
    evaluate_public_track_f,
    generate_sentinels,
    parse_image_digest,
    parse_memory_quantity,
    parse_worker_inspect,
    parse_worker_stats,
    run_ret_h_smoke,
    run_verification_transaction,
    scan_targets_for_sentinels,
    write_artifact,
)

QUERY_SENTINEL = "RET_H_SMOKE_Q_0123456789abcdef"
SOURCE_SENTINEL = "RET_H_SMOKE_S_fedcba9876543210"
SENTINELS = SmokeSentinels(query_sentinel=QUERY_SENTINEL, source_sentinel=SOURCE_SENTINEL)

LIVE_ENV = {"PUBLIC_TRACK_F_ENABLED": "false", "OPENAI_API_KEY": "sk-live-not-a-real-key"}

HEALTHY_WORKER = WorkerRuntimeFacts(
    image="acme/repo:ai-1.2.3",
    image_id="sha256:aaaa",
    memory_limit_bytes=EXPECTED_WORKER_MEMORY_LIMIT_BYTES,
    restart_count=0,
    oom_killed=False,
    state_status="running",
    health_status="healthy",
)


# --------------------------------------------------------------------------------------
# Preflight primitives
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["false", "False", "FALSE", "0", " false "])
def test_public_track_f_accepts_documented_false_literals(raw: str) -> None:
    assert evaluate_public_track_f(raw) is False


@pytest.mark.parametrize("raw", ["true", "True", "TRUE", "1"])
def test_public_track_f_detects_enabled(raw: str) -> None:
    assert evaluate_public_track_f(raw) is True


@pytest.mark.parametrize("raw", [None, "", "yes", "maybe"])
def test_public_track_f_unparseable_is_not_an_implicit_false(raw: str | None) -> None:
    assert evaluate_public_track_f(raw) is None


def test_embedding_credential_presence_never_returns_the_value() -> None:
    assert embedding_credential_present({"OPENAI_API_KEY": "sk-real"}) is True
    assert embedding_credential_present({"OPENAI_API_KEY": "   "}) is False
    assert embedding_credential_present({"OPENAI_API_KEY": "fake-key"}) is False
    assert embedding_credential_present({}) is False


def test_parse_worker_inspect_reads_allowlisted_fields_only() -> None:
    facts = parse_worker_inspect(
        [
            {
                "Image": "sha256:deadbeef",
                "RestartCount": 2,
                "Config": {"Image": "acme/repo:ai-1.0.0", "Env": ["OPENAI_API_KEY=sk-secret"]},
                "HostConfig": {"Memory": EXPECTED_WORKER_MEMORY_LIMIT_BYTES},
                "State": {"Status": "running", "OOMKilled": False, "Health": {"Status": "healthy"}},
            }
        ]
    )
    assert facts.memory_limit_bytes == EXPECTED_WORKER_MEMORY_LIMIT_BYTES
    assert facts.restart_count == 2
    assert facts.health_status == "healthy"
    # The container environment must never be carried into the parsed facts.
    assert "sk-secret" not in repr(facts)


def test_parse_image_digest_prefers_repo_digest() -> None:
    assert parse_image_digest([{"RepoDigests": ["acme/repo@sha256:abc"], "Id": "sha256:zzz"}]) == "acme/repo@sha256:abc"
    assert parse_image_digest([{"RepoDigests": [], "Id": "sha256:zzz"}]) == "sha256:zzz"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1GiB", 1073741824), ("742.4MiB", 778462822), ("512MB", 512000000)],
)
def test_parse_memory_quantity(raw: str, expected: int) -> None:
    assert parse_memory_quantity(raw) == expected


def test_parse_worker_stats() -> None:
    observation = parse_worker_stats({"MemUsage": "300MiB / 1GiB", "CPUPerc": "12.50%"})
    assert observation.memory_limit_bytes == EXPECTED_WORKER_MEMORY_LIMIT_BYTES
    assert observation.cpu_percent == pytest.approx(12.5)


def test_generated_sentinels_are_unique_and_only_exposed_as_digests() -> None:
    first = generate_sentinels()
    second = generate_sentinels()
    assert first.query_sentinel != second.query_sentinel
    assert first.query_sentinel_sha256 != first.source_sentinel_sha256
    assert len(first.query_sentinel_sha256) == 64


# --------------------------------------------------------------------------------------
# Privacy scan
# --------------------------------------------------------------------------------------


async def test_scan_reports_not_executed_when_a_target_cannot_be_read() -> None:
    results = await scan_targets_for_sentinels(
        [
            ScanTarget("ai_worker_logs", lambda: "clean worker log"),
            ScanTarget("fastapi_logs", reader=None),
            ScanTarget("redis_stream", lambda: (_ for _ in ()).throw(RuntimeError("redis down"))),
            ScanTarget("quarantine", reader=None, applicable=False),
        ],
        SENTINELS,
    )
    assert results["ai_worker_logs"] is ScanState.SCANNED_AND_NOT_FOUND
    assert results["fastapi_logs"] is ScanState.NOT_EXECUTED
    assert results["redis_stream"] is ScanState.NOT_EXECUTED
    assert results["quarantine"] is ScanState.NOT_APPLICABLE


async def test_scan_detects_a_leaked_sentinel() -> None:
    results = await scan_targets_for_sentinels(
        [ScanTarget("ai_worker_logs", lambda: f"query={QUERY_SENTINEL}")],
        SENTINELS,
    )
    assert results["ai_worker_logs"] is ScanState.FOUND


def test_unscanned_required_target_is_not_a_pass() -> None:
    verified, code = classify_privacy_scan(
        {
            "ai_worker_logs": ScanState.SCANNED_AND_NOT_FOUND,
            "fastapi_logs": ScanState.SCANNED_AND_NOT_FOUND,
            "redis_stream": ScanState.SCANNED_AND_NOT_FOUND,
            "redis_dlq": ScanState.NOT_EXECUTED,
        }
    )
    assert verified is False
    assert code == FAILED_BY_PRIVACY_SCAN_UNVERIFIED


def test_required_target_may_not_be_declared_not_applicable() -> None:
    verified, code = classify_privacy_scan(
        {
            "ai_worker_logs": ScanState.SCANNED_AND_NOT_FOUND,
            "fastapi_logs": ScanState.SCANNED_AND_NOT_FOUND,
            "redis_stream": ScanState.SCANNED_AND_NOT_FOUND,
            "redis_dlq": ScanState.NOT_APPLICABLE,
        }
    )
    assert verified is False
    assert code == FAILED_BY_PRIVACY_SCAN_UNVERIFIED


# --------------------------------------------------------------------------------------
# Independent verification transaction
# --------------------------------------------------------------------------------------


@dataclass
class RunRow:
    id: str
    status: str
    variant: str
    receipt_hash: str


@dataclass
class SignalRow:
    retrieval_method: str


@dataclass
class HitRow:
    selected: bool


class _Result:
    def __init__(self, data: Any) -> None:
        self._data = data

    def first(self) -> Any:
        return self._data

    def all(self) -> list[Any]:
        return list(self._data) if isinstance(self._data, list) else [self._data]


def _session_factory(results: list[Any]) -> Any:
    session = AsyncMock()
    session.execute.side_effect = [_Result(item) for item in results]

    class Factory:
        def __call__(self) -> Any:
            class Ctx:
                async def __aenter__(self) -> Any:
                    return session

                async def __aexit__(self, *args: Any) -> None:
                    return None

            return Ctx()

    return Factory()


def _healthy_rows(run_id: str, receipt_hash: str) -> list[Any]:
    return [
        RunRow(id=run_id, status="COMPLETED", variant="RET-H", receipt_hash=receipt_hash),
        [SignalRow("LEXICAL"), SignalRow("DENSE")],
        [HitRow(True), HitRow(False)],
    ]


async def test_verification_transaction_success() -> None:
    run_id, receipt_hash = str(uuid4()), "f" * 64
    details = await run_verification_transaction(
        session_factory=_session_factory(_healthy_rows(run_id, receipt_hash)),
        run_id=run_id,
        expected_receipt_hash=receipt_hash,
    )
    assert details["status"] == "COMPLETED"
    assert details["variant"] == "RET-H"
    assert details["selected_hits_count"] == 1


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"status": "RUNNING"}, "expected COMPLETED"),
        ({"variant": "RET-D"}, "expected RET-H"),
        ({"receipt_hash": "b" * 64}, "receipt_hash mismatch"),
    ],
)
async def test_verification_transaction_rejects_bad_terminal_state(mutation: dict[str, str], match: str) -> None:
    run_id, receipt_hash = str(uuid4()), "f" * 64
    rows = _healthy_rows(run_id, receipt_hash)
    rows[0] = replace(rows[0], **mutation)
    with pytest.raises(AssertionError, match=match):
        await run_verification_transaction(
            session_factory=_session_factory(rows),
            run_id=run_id,
            expected_receipt_hash=receipt_hash,
        )


@pytest.mark.parametrize(
    ("signals", "match"),
    [
        ([SignalRow("DENSE")], "No lexical retrieval_signal"),
        ([SignalRow("LEXICAL")], "No dense retrieval_signal"),
        ([], "No retrieval_signal rows"),
    ],
)
async def test_verification_transaction_requires_both_signal_families(signals: list[SignalRow], match: str) -> None:
    run_id, receipt_hash = str(uuid4()), "f" * 64
    rows = _healthy_rows(run_id, receipt_hash)
    rows[1] = signals
    with pytest.raises(AssertionError, match=match):
        await run_verification_transaction(
            session_factory=_session_factory(rows),
            run_id=run_id,
            expected_receipt_hash=receipt_hash,
        )


async def test_verification_transaction_requires_a_selected_hit() -> None:
    run_id, receipt_hash = str(uuid4()), "f" * 64
    rows = _healthy_rows(run_id, receipt_hash)
    rows[2] = [HitRow(False)]
    with pytest.raises(AssertionError, match="No selected retrieval_hit"):
        await run_verification_transaction(
            session_factory=_session_factory(rows),
            run_id=run_id,
            expected_receipt_hash=receipt_hash,
        )


# --------------------------------------------------------------------------------------
# End-to-end orchestration
# --------------------------------------------------------------------------------------


def _outcome(run_id: str, receipt_hash: str) -> Any:
    outcome = MagicMock()
    outcome.status = MagicMock(name="SUCCEEDED")
    outcome.status.name = "SUCCEEDED"
    outcome.persisted_receipt = MagicMock(run_id=run_id, receipt_hash=receipt_hash)
    outcome.gate_outcome = MagicMock(selected_hits=(MagicMock(),))
    return outcome


def _dependencies(**overrides: Any) -> LiveSmokeDependencies:
    run_id, receipt_hash = str(uuid4()), "e" * 64

    async def _pass() -> GateNegativeResult:
        return GateNegativeResult(executed=True, fail_closed=True)

    base: dict[str, Any] = {
        "session_factory": MagicMock(),
        "verification_session_factory": _session_factory(_healthy_rows(run_id, receipt_hash)),
        "search_port": MagicMock(),
        "text_embedding_port": MagicMock(),
        "run_store": MagicMock(),
        "eligibility_verifier": MagicMock(),
        "hybrid_retrieve_request": MagicMock(),
        "execution_fn": AsyncMock(return_value=_outcome(run_id, receipt_hash)),
        "receipt_verifier": lambda _receipt: True,
        "stale_case": _pass,
        "locator_mismatch_case": _pass,
        "scan_targets": (
            ScanTarget("ai_worker_logs", lambda: "clean"),
            ScanTarget("fastapi_logs", lambda: "clean"),
            ScanTarget("redis_stream", lambda: "clean"),
            ScanTarget("redis_dlq", lambda: "clean"),
            ScanTarget("quarantine", reader=None, applicable=False),
        ),
        "knowledge_index_ref": "rag-synthetic-index:1.0.0",
    }
    base.update(overrides)
    return LiveSmokeDependencies(**base)


async def _run(**overrides: Any) -> Any:
    from app.release_validation.ret_h_synthetic_smoke import ResourceObservation

    kwargs: dict[str, Any] = {
        "mode": "aws-live",
        "environment": LIVE_ENV,
        "git_commit_sha": "0c31db9e",
        "dependencies": _dependencies(),
        "worker_facts": HEALTHY_WORKER,
        "worker_image_digest": "acme/repo@sha256:abc",
        "resource_observation": ResourceObservation(
            memory_usage_bytes=300 * 1024**2,
            memory_limit_bytes=EXPECTED_WORKER_MEMORY_LIMIT_BYTES,
            cpu_percent=4.0,
        ),
        "sentinels": SENTINELS,
    }
    kwargs.update(overrides)
    return await run_ret_h_smoke(**kwargs)


async def test_happy_path_reports_success_with_every_check_verified() -> None:
    receipt = await _run()
    assert receipt.status == STATUS_SUCCESS
    assert receipt.execution_transaction_verified is True
    assert receipt.verification_transaction_verified is True
    assert receipt.receipt_verified is True
    assert receipt.evidence_gate_positive_verified is True
    assert receipt.evidence_gate_stale_fail_closed_verified is True
    assert receipt.evidence_gate_locator_fail_closed_verified is True
    assert receipt.privacy_scan_verified is True
    assert receipt.resource_observation_verified is True
    assert receipt.public_track_f_enabled is False


async def test_local_preflight_never_executes() -> None:
    receipt = await _run(mode="local-preflight")
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_LOCAL_PREFLIGHT
    assert receipt.retrieval_run_id is None


async def test_public_track_f_enabled_blocks_execution() -> None:
    receipt = await _run(environment={**LIVE_ENV, "PUBLIC_TRACK_F_ENABLED": "true"})
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_PUBLIC_TRACK_F_ENABLED


async def test_unverifiable_public_track_f_blocks_execution() -> None:
    receipt = await _run(environment={"OPENAI_API_KEY": "sk-live"})
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_PUBLIC_TRACK_F_UNVERIFIED


async def test_missing_embedding_credential_blocks_execution() -> None:
    receipt = await _run(environment={"PUBLIC_TRACK_F_ENABLED": "false"})
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL


async def test_unobserved_deployment_identity_blocks_execution() -> None:
    receipt = await _run(worker_facts=None)
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_DEPLOYMENT_IDENTITY_UNVERIFIED


async def test_memory_limit_mismatch_fails() -> None:
    receipt = await _run(worker_facts=replace(HEALTHY_WORKER, memory_limit_bytes=2 * 1024**3))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_WORKER_MEMORY_LIMIT


async def test_oom_killed_fails() -> None:
    receipt = await _run(worker_facts=replace(HEALTHY_WORKER, oom_killed=True))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_WORKER_OOM_KILLED


@pytest.mark.parametrize(
    "absent",
    [
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
    ],
)
async def test_any_missing_runtime_dependency_blocks_execution(absent: str) -> None:
    receipt = await _run(dependencies=_dependencies(**{absent: None}))
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING
    assert absent in (receipt.error_message or "")


async def test_missing_privacy_scan_target_blocks_execution() -> None:
    receipt = await _run(dependencies=_dependencies(scan_targets=()))
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING


async def test_receipt_mismatch_fails() -> None:
    receipt = await _run(dependencies=_dependencies(receipt_verifier=lambda _r: False))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_RECEIPT_MISMATCH
    assert receipt.receipt_verified is False


async def test_non_terminal_retrieval_run_fails() -> None:
    run_id, receipt_hash = str(uuid4()), "e" * 64
    rows = _healthy_rows(run_id, receipt_hash)
    rows[0] = replace(rows[0], status="RUNNING")
    receipt = await _run(
        dependencies=_dependencies(
            verification_session_factory=_session_factory(rows),
            execution_fn=AsyncMock(return_value=_outcome(run_id, receipt_hash)),
        )
    )
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_RUN_VERIFICATION
    assert receipt.verification_transaction_verified is False


async def test_evidence_gate_positive_without_selection_fails() -> None:
    outcome = _outcome(str(uuid4()), "e" * 64)
    outcome.gate_outcome = MagicMock(selected_hits=())
    receipt = await _run(dependencies=_dependencies(execution_fn=AsyncMock(return_value=outcome)))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_EVIDENCE_GATE_FAIL_OPEN


@pytest.mark.parametrize("case", ["stale_case", "locator_mismatch_case"])
async def test_unexecuted_negative_case_is_never_a_pass(case: str) -> None:
    async def _never_ran() -> GateNegativeResult:
        return GateNegativeResult(executed=False, fail_closed=False, message="dependency unavailable")

    receipt = await _run(dependencies=_dependencies(**{case: _never_ran}))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_EVIDENCE_GATE_UNVERIFIED


@pytest.mark.parametrize("case", ["stale_case", "locator_mismatch_case"])
async def test_fail_open_negative_case_fails(case: str) -> None:
    async def _fail_open() -> GateNegativeResult:
        return GateNegativeResult(executed=True, fail_closed=False, message="tampered candidate was selected")

    receipt = await _run(dependencies=_dependencies(**{case: _fail_open}))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_EVIDENCE_GATE_FAIL_OPEN


async def test_leaked_sentinel_fails() -> None:
    targets = (
        ScanTarget("ai_worker_logs", lambda: f"processing {QUERY_SENTINEL}"),
        ScanTarget("fastapi_logs", lambda: "clean"),
        ScanTarget("redis_stream", lambda: "clean"),
        ScanTarget("redis_dlq", lambda: "clean"),
    )
    receipt = await _run(dependencies=_dependencies(scan_targets=targets))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_SENTINEL_FOUND
    assert receipt.privacy_scan_verified is False


async def test_unscanned_log_target_fails() -> None:
    targets = (
        ScanTarget("ai_worker_logs", lambda: "clean"),
        ScanTarget("fastapi_logs", lambda: "clean"),
        ScanTarget("redis_stream", lambda: "clean"),
        ScanTarget("redis_dlq", reader=None),
    )
    receipt = await _run(dependencies=_dependencies(scan_targets=targets))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_PRIVACY_SCAN_UNVERIFIED


async def test_missing_resource_observation_fails() -> None:
    receipt = await _run(resource_observation=None)
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED


# --------------------------------------------------------------------------------------
# Artifact
# --------------------------------------------------------------------------------------


async def test_artifact_is_sanitized_and_records_only_sentinel_digests(tmp_path: Any) -> None:
    receipt = await _run()
    output = tmp_path / "smoke.json"
    payload = write_artifact(receipt, output)

    assert QUERY_SENTINEL not in payload
    assert SOURCE_SENTINEL not in payload
    assert LIVE_ENV["OPENAI_API_KEY"] not in payload

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["status"] == STATUS_SUCCESS
    assert document["deployment"]["kind"] == "EC2_DOCKER_COMPOSE"
    assert document["deployment"]["public_track_f_enabled"] is False
    assert document["retrieval"]["variant"] == "RET-H"
    assert document["retrieval"]["reranker_calls"] == 0
    assert document["fixture"]["query_sentinel_sha256"] == SENTINELS.query_sentinel_sha256
    assert document["fixture"]["embedding_dimension"] == 1536
    assert document["privacy"]["targets"]["quarantine"] == "NOT_APPLICABLE"
    assert "RET-HR not executed" in document["limitations"]


async def test_blocked_artifact_never_claims_a_pass(tmp_path: Any) -> None:
    receipt = await _run(mode="local-preflight")
    document = json.loads(write_artifact(receipt, tmp_path / "blocked.json"))
    assert document["status"] == AWS_SMOKE_NOT_EXECUTED
    retrieval = document["retrieval"]
    gate = document["evidence_gate"]
    assert retrieval["execution_transaction_verified"] is False
    assert retrieval["verification_transaction_verified"] is False
    assert retrieval["receipt_verified"] is False
    assert all(value is False for value in gate.values())
    assert document["privacy"]["scan_verified"] is False
    assert document["resources"]["observation_verified"] is False
