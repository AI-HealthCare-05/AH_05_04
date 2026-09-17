"""Fail-closed regression tests for the Issue #178 RET-H AWS synthetic smoke.

Every test here exists to pin one rule: a verification that did not actually run
and pass can never be reported as a PASS.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.release_validation.ret_h_synthetic_smoke import (
    AWS_SMOKE_NOT_EXECUTED,
    BLOCKED_BY_AWAITING_PRIVACY_OBSERVATION,
    BLOCKED_BY_DEPLOYMENT_IDENTITY_UNVERIFIED,
    BLOCKED_BY_LOCAL_PREFLIGHT,
    BLOCKED_BY_NON_SYNTHETIC_FIXTURE,
    BLOCKED_BY_PRIVACY_OBSERVATION_UNBOUND,
    BLOCKED_BY_PUBLIC_TRACK_F_ENABLED,
    BLOCKED_BY_PUBLIC_TRACK_F_UNVERIFIED,
    BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL,
    BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING,
    BLOCKED_BY_SENTINEL_BINDING_UNVERIFIED,
    EXPECTED_WORKER_MEMORY_LIMIT_BYTES,
    FAILED_BY_EVIDENCE_GATE_FAIL_OPEN,
    FAILED_BY_EVIDENCE_GATE_UNVERIFIED,
    FAILED_BY_PRIVACY_OBSERVATION_UNBOUND,
    FAILED_BY_PRIVACY_SCAN_UNVERIFIED,
    FAILED_BY_RECEIPT_MISMATCH,
    FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED,
    FAILED_BY_RUN_VERIFICATION,
    FAILED_BY_SELECTED_SOURCE_SENTINEL_UNBOUND,
    FAILED_BY_SENTINEL_FOUND,
    FAILED_BY_WORKER_MEMORY_LIMIT,
    FAILED_BY_WORKER_OOM_KILLED,
    REQUIRED_SCAN_TARGETS,
    SCHEMA_VERSION,
    STATUS_FAILED,
    STATUS_SUCCESS,
    CheckResult,
    GateNegativeResult,
    LiveSmokeDependencies,
    PrivacyObservation,
    ScanState,
    ScanTarget,
    SmokeSentinels,
    WorkerRuntimeFacts,
    classify_privacy_scan,
    embedding_credential_present,
    evaluate_public_track_f,
    finalize_smoke_artifact,
    generate_sentinels,
    parse_image_digest,
    parse_memory_quantity,
    parse_worker_inspect,
    parse_worker_stats,
    run_ret_h_smoke,
    run_verification_transaction,
    scan_targets_for_sentinels,
    verify_privacy_observation_binding,
    verify_query_sentinel_binding,
    write_artifact,
)

QUERY_SENTINEL = "RET_H_SMOKE_Q_0123456789abcdef"
SOURCE_SENTINEL = "RET_H_SMOKE_S_fedcba9876543210"
SENTINELS = SmokeSentinels(query_sentinel=QUERY_SENTINEL, source_sentinel=SOURCE_SENTINEL)
SUBMITTED_QUERY = f"합성 스모크 질문 {QUERY_SENTINEL}"
APPROVED_QUERY_SHA256 = hashlib.sha256(SUBMITTED_QUERY.encode("utf-8")).hexdigest()

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
            "smoke_one_shot_logs": ScanState.SCANNED_AND_NOT_FOUND,
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
            "smoke_one_shot_logs": ScanState.SCANNED_AND_NOT_FOUND,
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
    hit = MagicMock()
    hit.provenance.knowledge_chunk_id = "chunk-default"
    outcome.gate_outcome = MagicMock(selected_hits=(hit,))
    return outcome


def _dependencies(**overrides: Any) -> LiveSmokeDependencies:
    run_id, receipt_hash = str(uuid4()), "e" * 64

    async def _pass() -> GateNegativeResult:
        return GateNegativeResult(executed=True, fail_closed=True)

    async def _check_pass() -> CheckResult:
        return CheckResult(executed=True, passed=True)

    async def _selected_pass(_selected_chunk_ids: Any) -> CheckResult:
        return CheckResult(executed=True, passed=True)

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
        "source_sentinel_binding_case": _check_pass,
        "fixture_authenticity_case": _check_pass,
        "selected_source_binding_case": _selected_pass,
        "stale_case": _pass,
        "locator_mismatch_case": _pass,
        "scan_targets": (
            ScanTarget("ai_worker_logs", lambda: "clean"),
            ScanTarget("fastapi_logs", lambda: "clean"),
            ScanTarget("redis_stream", lambda: "clean"),
            ScanTarget("redis_dlq", lambda: "clean"),
            ScanTarget("smoke_one_shot_logs", lambda: "clean"),
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
        "submitted_query": SUBMITTED_QUERY,
        "approved_query_sha256": APPROVED_QUERY_SHA256,
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
        "source_sentinel_binding_case",
        "fixture_authenticity_case",
        "selected_source_binding_case",
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
        ScanTarget("smoke_one_shot_logs", lambda: "clean"),
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
        ScanTarget("smoke_one_shot_logs", lambda: "clean"),
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


# --------------------------------------------------------------------------------------
# Sentinel binding and synthetic-fixture authenticity
# --------------------------------------------------------------------------------------


def test_query_binding_accepts_the_approved_query() -> None:
    assert (
        verify_query_sentinel_binding(
            synthetic_query=SUBMITTED_QUERY,
            sentinels=SENTINELS,
            approved_query_sha256=APPROVED_QUERY_SHA256,
        ).verified
        is True
    )


@pytest.mark.parametrize(
    ("query", "approved", "fragment"),
    [
        ("합성 스모크 질문 without any marker", None, "approved synthetic_query_sha256"),
        ("   ", APPROVED_QUERY_SHA256, "empty synthetic query"),
        # A different question carrying the marker must not reach the provider.
        (f"완전히 다른 질문 {QUERY_SENTINEL}", APPROVED_QUERY_SHA256, "does not match the approved"),
        (SUBMITTED_QUERY, "0" * 64, "does not match the approved"),
        (
            f"{QUERY_SENTINEL} and also {SOURCE_SENTINEL}",
            hashlib.sha256(f"{QUERY_SENTINEL} and also {SOURCE_SENTINEL}".encode()).hexdigest(),
            "distinguishable",
        ),
    ],
)
def test_query_binding_rejects_unapproved_or_unbound_queries(query: str, approved: str | None, fragment: str) -> None:
    result = verify_query_sentinel_binding(
        synthetic_query=query,
        sentinels=SENTINELS,
        approved_query_sha256=approved,
    )
    assert result.verified is False
    assert fragment in result.message


async def test_query_not_carrying_the_sentinel_blocks_before_execution() -> None:
    execution = AsyncMock()
    receipt = await _run(
        submitted_query="질문에 sentinel 이 없다",
        approved_query_sha256=hashlib.sha256("질문에 sentinel 이 없다".encode()).hexdigest(),
        dependencies=_dependencies(execution_fn=execution),
    )
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_SENTINEL_BINDING_UNVERIFIED
    # Nothing may reach the provider or the database.
    execution.assert_not_awaited()
    assert receipt.retrieval_run_id is None


async def test_missing_fixture_sentinels_block_before_execution() -> None:
    execution = AsyncMock()
    receipt = await _run(sentinels=None, dependencies=_dependencies(execution_fn=execution))
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_SENTINEL_BINDING_UNVERIFIED
    execution.assert_not_awaited()


async def test_unindexed_source_sentinel_blocks_before_execution() -> None:
    async def _unbound() -> CheckResult:
        return CheckResult(executed=True, passed=False, message="no indexed chunk carries the sentinel")

    execution = AsyncMock()
    receipt = await _run(dependencies=_dependencies(source_sentinel_binding_case=_unbound, execution_fn=execution))
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_SENTINEL_BINDING_UNVERIFIED
    execution.assert_not_awaited()


async def test_unexecuted_source_sentinel_check_is_never_a_pass() -> None:
    async def _never_ran() -> CheckResult:
        return CheckResult(executed=False, passed=False, message="database unreachable")

    execution = AsyncMock()
    receipt = await _run(dependencies=_dependencies(source_sentinel_binding_case=_never_ran, execution_fn=execution))
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_SENTINEL_BINDING_UNVERIFIED
    execution.assert_not_awaited()


@pytest.mark.parametrize(
    "result",
    [
        CheckResult(executed=True, passed=False, message="pinned knowledge index is not an approved synthetic index"),
        CheckResult(executed=False, passed=False, message="database unreachable"),
    ],
)
async def test_non_synthetic_or_unproven_fixture_blocks_provider_and_db(result: CheckResult) -> None:
    async def _case() -> CheckResult:
        return result

    execution = AsyncMock()
    receipt = await _run(dependencies=_dependencies(fixture_authenticity_case=_case, execution_fn=execution))
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_NON_SYNTHETIC_FIXTURE
    # Zero provider calls and zero Retrieval Run writes.
    execution.assert_not_awaited()
    assert receipt.retrieval_run_id is None
    assert receipt.fixture_authenticity_verified is False


async def test_sentinel_leaked_only_on_stderr_is_still_detected() -> None:
    """docker logs replays container stderr on its own stderr; the reader must merge it."""
    from app.release_validation.ret_h_synthetic_smoke import default_command_runner

    combined = default_command_runner(
        [
            "sh",
            "-c",
            f"echo out-only; echo {QUERY_SENTINEL} 1>&2",
        ]
    )
    assert QUERY_SENTINEL in combined
    assert "out-only" in combined


async def test_verification_failure_message_does_not_embed_raw_assertion_text(tmp_path: Any) -> None:
    async def _boom(**_kwargs: Any) -> Any:
        raise AssertionError(f"leaky detail {QUERY_SENTINEL}")

    from app.release_validation import ret_h_synthetic_smoke as module

    original = module.run_verification_transaction
    module.run_verification_transaction = _boom  # type: ignore[assignment]
    try:
        receipt = await _run()
    finally:
        module.run_verification_transaction = original  # type: ignore[assignment]

    assert receipt.status == STATUS_FAILED
    payload = write_artifact(receipt, tmp_path / "failed.json")
    assert QUERY_SENTINEL not in payload
    assert "leaky detail" not in payload


# --------------------------------------------------------------------------------------
# Run-bound, post-execution privacy observation
# --------------------------------------------------------------------------------------

EXEC_START = datetime(2026, 9, 17, 3, 0, 0, tzinfo=UTC)
EXEC_END = datetime(2026, 9, 17, 3, 0, 30, tzinfo=UTC)
RUN_ID = "11111111-2222-4333-8444-555555555555"

CLEAN_TARGETS = {name: ScanState.SCANNED_AND_NOT_FOUND for name in REQUIRED_SCAN_TARGETS}


def _observation(**overrides: Any) -> PrivacyObservation:
    base: dict[str, Any] = {
        "retrieval_run_id": RUN_ID,
        "query_sentinel_sha256": SENTINELS.query_sentinel_sha256,
        "source_sentinel_sha256": SENTINELS.source_sentinel_sha256,
        "scanned_since": EXEC_START - timedelta(seconds=5),
        "scanned_at": EXEC_END + timedelta(seconds=5),
        "targets": dict(CLEAN_TARGETS),
    }
    base.update(overrides)
    return PrivacyObservation(**base)


def _binding(observation: PrivacyObservation) -> Any:
    return verify_privacy_observation_binding(
        observation,
        retrieval_run_id=RUN_ID,
        sentinels=SENTINELS,
        execution_started_at=EXEC_START,
        execution_finished_at=EXEC_END,
    )


def test_bound_observation_covering_the_execution_is_accepted() -> None:
    assert _binding(_observation()).verified is True


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"retrieval_run_id": "99999999-2222-4333-8444-555555555555"}, "different Retrieval Run"),
        ({"query_sentinel_sha256": "0" * 64}, "different query sentinel"),
        ({"source_sentinel_sha256": "0" * 64}, "different Source sentinel"),
        # A scan taken before the run finished cannot see a leak the run produced.
        ({"scanned_at": EXEC_END - timedelta(seconds=1)}, "before the execution finished"),
        ({"scanned_since": EXEC_START + timedelta(seconds=1)}, "starts after the execution began"),
    ],
)
def test_stale_or_different_run_observation_is_rejected(override: dict[str, Any], fragment: str) -> None:
    result = _binding(_observation(**override))
    assert result.verified is False
    assert fragment in result.message


async def test_execute_phase_never_certifies_privacy() -> None:
    receipt = await _run(defer_privacy=True)
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_AWAITING_PRIVACY_OBSERVATION
    assert receipt.privacy_scan_verified is False
    # Everything before privacy is still recorded, including the window finalize needs.
    assert receipt.execution_transaction_verified is True
    assert receipt.receipt_verified is True
    assert receipt.execution_started_at is not None
    assert receipt.execution_finished_at is not None


async def _interim() -> dict[str, Any]:
    receipt = await _run(defer_privacy=True)
    document = receipt.to_artifact()
    document["retrieval"]["retrieval_run_id"] = RUN_ID
    document["retrieval"]["execution_started_at"] = EXEC_START.isoformat()
    document["retrieval"]["execution_finished_at"] = EXEC_END.isoformat()
    return document


async def test_finalize_completes_a_clean_bound_scan() -> None:
    final = finalize_smoke_artifact(await _interim(), privacy_observation=_observation(), sentinels=SENTINELS)
    assert final["status"] == STATUS_SUCCESS
    assert final["privacy"]["scan_verified"] is True
    assert final["blocked_code"] is None


async def test_finalize_rejects_a_different_run_observation() -> None:
    final = finalize_smoke_artifact(
        await _interim(),
        privacy_observation=_observation(retrieval_run_id="99999999-2222-4333-8444-555555555555"),
        sentinels=SENTINELS,
    )
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == FAILED_BY_PRIVACY_OBSERVATION_UNBOUND
    assert final["privacy"]["scan_verified"] is False


async def test_finalize_rejects_a_scan_taken_before_the_run_finished() -> None:
    final = finalize_smoke_artifact(
        await _interim(),
        privacy_observation=_observation(scanned_at=EXEC_END - timedelta(seconds=1)),
        sentinels=SENTINELS,
    )
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == FAILED_BY_PRIVACY_OBSERVATION_UNBOUND


async def test_finalize_fails_when_the_one_shot_log_was_not_scanned() -> None:
    targets = dict(CLEAN_TARGETS)
    targets["smoke_one_shot_logs"] = ScanState.NOT_EXECUTED
    final = finalize_smoke_artifact(
        await _interim(), privacy_observation=_observation(targets=targets), sentinels=SENTINELS
    )
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == FAILED_BY_PRIVACY_SCAN_UNVERIFIED


async def test_finalize_fails_when_the_run_leaked_into_its_own_log() -> None:
    targets = dict(CLEAN_TARGETS)
    targets["smoke_one_shot_logs"] = ScanState.FOUND
    final = finalize_smoke_artifact(
        await _interim(), privacy_observation=_observation(targets=targets), sentinels=SENTINELS
    )
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == FAILED_BY_SENTINEL_FOUND


async def test_finalize_refuses_an_artifact_that_is_not_awaiting_privacy() -> None:
    receipt = await _run()
    final = finalize_smoke_artifact(receipt.to_artifact(), privacy_observation=_observation(), sentinels=SENTINELS)
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == BLOCKED_BY_PRIVACY_OBSERVATION_UNBOUND


def test_zero_memory_usage_from_a_failed_host_conversion_is_rejected() -> None:
    from app.release_validation.ret_h_synthetic_smoke import (
        OBSERVATION_SCHEMA_VERSION,
        parse_observation_document,
    )

    payload = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "worker": {"memory_limit_bytes": EXPECTED_WORKER_MEMORY_LIMIT_BYTES},
        "resources": {"memory_usage_bytes": 0},
    }
    with pytest.raises(ValueError, match="non-positive memory usage"):
        parse_observation_document(payload)


# --------------------------------------------------------------------------------------
# WATCH 1: execute must not require post-execution scan evidence
# --------------------------------------------------------------------------------------


async def test_execute_is_not_blocked_by_absent_post_execution_scan_evidence() -> None:
    """The execute phase runs before any scan exists, so scan targets cannot gate it."""
    execution = AsyncMock(return_value=_outcome(RUN_ID, "e" * 64))
    receipt = await _run(
        defer_privacy=True,
        dependencies=_dependencies(scan_targets=(), execution_fn=execution),
    )
    assert receipt.blocked_code != BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_AWAITING_PRIVACY_OBSERVATION
    execution.assert_awaited_once()
    assert receipt.retrieval_run_id == RUN_ID


async def test_non_deferred_run_still_requires_scan_targets() -> None:
    receipt = await _run(dependencies=_dependencies(scan_targets=()))
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING


def test_one_shot_logs_are_read_before_the_container_is_removed() -> None:
    """--rm would destroy the log before the scan; logs must precede cleanup."""
    from app.release_validation.ret_h_synthetic_smoke import scan_and_cleanup_one_shot

    calls: list[list[str]] = []

    def runner(args: Any) -> str:
        calls.append(list(args))
        return "clean worker output" if args[1] == "logs" else ""

    state, cleanup = scan_and_cleanup_one_shot(
        container="ret-h-smoke-oneshot-abc123", sentinels=SENTINELS, runner=runner
    )
    assert state is ScanState.SCANNED_AND_NOT_FOUND
    assert cleanup.verified is True
    assert [c[1] for c in calls] == ["logs", "rm"]
    assert "ret-h-smoke-oneshot-abc123" in calls[0]


def test_one_shot_log_leak_is_detected_and_container_still_removed() -> None:
    from app.release_validation.ret_h_synthetic_smoke import scan_and_cleanup_one_shot

    calls: list[list[str]] = []

    def runner(args: Any) -> str:
        calls.append(list(args))
        return f"submitted {QUERY_SENTINEL}" if args[1] == "logs" else ""

    state, cleanup = scan_and_cleanup_one_shot(container="c1", sentinels=SENTINELS, runner=runner)
    assert state is ScanState.FOUND
    assert cleanup.verified is True
    assert [c[1] for c in calls] == ["logs", "rm"]


def test_one_shot_cleanup_failure_is_surfaced_not_swallowed() -> None:
    from app.release_validation.ret_h_synthetic_smoke import scan_and_cleanup_one_shot

    def runner(args: Any) -> str:
        if args[1] == "rm":
            raise RuntimeError("container still in use")
        return "clean"

    state, cleanup = scan_and_cleanup_one_shot(container="c1", sentinels=SENTINELS, runner=runner)
    assert state is ScanState.SCANNED_AND_NOT_FOUND
    assert cleanup.verified is False
    assert "cleanup" in cleanup.message.lower() or "RuntimeError" in cleanup.message


def test_one_shot_container_names_are_run_specific() -> None:
    from app.release_validation.ret_h_synthetic_smoke import one_shot_container_name

    first = one_shot_container_name()
    second = one_shot_container_name()
    assert first != second
    assert first.startswith("ret-h-smoke-oneshot-")


# --------------------------------------------------------------------------------------
# WATCH 2: resource evidence must exist before the interim artifact
# --------------------------------------------------------------------------------------


async def test_execute_without_resource_observation_fails_instead_of_awaiting_privacy() -> None:
    receipt = await _run(defer_privacy=True, resource_observation=None)
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED


async def test_execute_with_zero_memory_usage_fails() -> None:
    from app.release_validation.ret_h_synthetic_smoke import ResourceObservation

    receipt = await _run(
        defer_privacy=True,
        resource_observation=ResourceObservation(
            memory_usage_bytes=0, memory_limit_bytes=EXPECTED_WORKER_MEMORY_LIMIT_BYTES, cpu_percent=1.0
        ),
    )
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED


async def test_execute_with_mismatched_resource_limit_fails() -> None:
    from app.release_validation.ret_h_synthetic_smoke import ResourceObservation

    receipt = await _run(
        defer_privacy=True,
        resource_observation=ResourceObservation(
            memory_usage_bytes=1024, memory_limit_bytes=2 * 1024**3, cpu_percent=1.0
        ),
    )
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_WORKER_MEMORY_LIMIT


async def test_interim_preserves_resource_measurements() -> None:
    receipt = await _run(defer_privacy=True)
    assert receipt.resource_observation_verified is True
    assert receipt.worker_memory_usage_bytes == 300 * 1024**2
    assert receipt.worker_memory_limit_bytes == EXPECTED_WORKER_MEMORY_LIMIT_BYTES
    assert receipt.worker_cpu_percent == pytest.approx(4.0)
    assert receipt.restart_count == 0
    assert receipt.container_health == "healthy"

    document = receipt.to_artifact()["resources"]
    assert document["observation_verified"] is True
    assert document["worker_memory_usage_bytes"] == 300 * 1024**2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(observation_verified=False),
        lambda r: r.update(worker_memory_usage_bytes=0),
        lambda r: r.pop("worker_memory_usage_bytes", None),
        lambda r: r.update(worker_memory_limit_bytes=2 * 1024**3),
        lambda r: r.update(oom_killed=True),
    ],
)
async def test_finalize_refuses_an_interim_without_valid_resource_evidence(mutate: Any) -> None:
    document = await _interim()
    mutate(document["resources"])
    final = finalize_smoke_artifact(document, privacy_observation=_observation(), sentinels=SENTINELS)
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == FAILED_BY_RESOURCE_OBSERVATION_UNVERIFIED


# --------------------------------------------------------------------------------------
# WATCH 3: the candidates actually selected must carry the Source sentinel
# --------------------------------------------------------------------------------------


def _hit_with_chunk(chunk_id: str) -> Any:
    hit = MagicMock()
    hit.provenance.knowledge_chunk_id = chunk_id
    return hit


async def test_selected_chunk_ids_are_passed_to_the_source_binding_check() -> None:
    seen: dict[str, Any] = {}

    async def _case(selected_chunk_ids: Any) -> CheckResult:
        seen["ids"] = tuple(selected_chunk_ids)
        return CheckResult(executed=True, passed=True)

    outcome = _outcome(RUN_ID, "e" * 64)
    outcome.gate_outcome = MagicMock(selected_hits=(_hit_with_chunk("chunk-a"), _hit_with_chunk("chunk-b")))
    receipt = await _run(
        dependencies=_dependencies(execution_fn=AsyncMock(return_value=outcome), selected_source_binding_case=_case)
    )
    assert receipt.status == STATUS_SUCCESS
    assert seen["ids"] == ("chunk-a", "chunk-b")
    assert receipt.selected_source_binding_verified is True


async def test_marker_only_on_an_unselected_chunk_blocks_success() -> None:
    """Pre-execution binding can pass while the selected Source has no marker."""

    async def _case(_ids: Any) -> CheckResult:
        return CheckResult(
            executed=True, passed=False, message="a selected candidate does not carry the declared Source sentinel"
        )

    receipt = await _run(dependencies=_dependencies(selected_source_binding_case=_case))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_SELECTED_SOURCE_SENTINEL_UNBOUND
    assert receipt.selected_source_binding_verified is False


async def test_unexecuted_selected_source_check_is_never_a_pass() -> None:
    async def _case(_ids: Any) -> CheckResult:
        return CheckResult(executed=False, passed=False, message="database unreachable")

    receipt = await _run(dependencies=_dependencies(selected_source_binding_case=_case))
    assert receipt.status == STATUS_FAILED
    assert receipt.blocked_code == FAILED_BY_SELECTED_SOURCE_SENTINEL_UNBOUND


async def test_missing_selected_source_binding_dependency_blocks_execution() -> None:
    execution = AsyncMock()
    receipt = await _run(dependencies=_dependencies(selected_source_binding_case=None, execution_fn=execution))
    assert receipt.status == AWS_SMOKE_NOT_EXECUTED
    assert receipt.blocked_code == BLOCKED_BY_RUNTIME_DEPENDENCY_MISSING
    execution.assert_not_awaited()


async def test_interim_records_the_selected_source_binding() -> None:
    receipt = await _run(defer_privacy=True)
    assert receipt.selected_source_binding_verified is True
    assert receipt.to_artifact()["fixture"]["selected_source_binding_verified"] is True


# --------------------------------------------------------------------------------------
# End-to-end: fixture -> execute -> selected-hit check -> interim -> host scan -> finalize
# --------------------------------------------------------------------------------------


async def _execute_then_finalize(
    *,
    selected_chunk_ids: tuple[str, ...],
    corpus_with_marker: set[str],
    one_shot_log: str = "clean one-shot output",
) -> dict[str, Any]:
    """Drive the whole composition with a mocked production execution.

    ``corpus_with_marker`` is the set of chunk ids whose Source text carries the Source
    sentinel, so a marker can be placed on a chunk that is never selected.
    """
    outcome = _outcome(RUN_ID, "e" * 64)
    outcome.gate_outcome = MagicMock(selected_hits=tuple(_hit_with_chunk(c) for c in selected_chunk_ids))

    async def _selected_case(ids: Any) -> CheckResult:
        missing = [c for c in ids if c not in corpus_with_marker]
        if missing:
            return CheckResult(
                executed=True,
                passed=False,
                message="a selected candidate does not carry the declared Source sentinel",
            )
        return CheckResult(executed=True, passed=True)

    interim = await _run(
        defer_privacy=True,
        dependencies=_dependencies(
            execution_fn=AsyncMock(return_value=outcome),
            selected_source_binding_case=_selected_case,
            scan_targets=(),
        ),
    )
    document = interim.to_artifact()
    if interim.status != AWS_SMOKE_NOT_EXECUTED:
        return document

    document["retrieval"]["execution_started_at"] = EXEC_START.isoformat()
    document["retrieval"]["execution_finished_at"] = EXEC_END.isoformat()

    # Host phase: the one-shot container is still present, so its log can be scanned.
    from app.release_validation.ret_h_synthetic_smoke import scan_and_cleanup_one_shot

    def runner(args: Any) -> str:
        return one_shot_log if args[1] == "logs" else ""

    one_shot_state, cleanup = scan_and_cleanup_one_shot(
        container="ret-h-smoke-oneshot-e2e", sentinels=SENTINELS, runner=runner
    )
    assert cleanup.verified is True

    targets = dict(CLEAN_TARGETS)
    targets["smoke_one_shot_logs"] = one_shot_state
    return finalize_smoke_artifact(document, privacy_observation=_observation(targets=targets), sentinels=SENTINELS)


async def test_end_to_end_succeeds_when_every_selected_candidate_carries_the_marker() -> None:
    final = await _execute_then_finalize(
        selected_chunk_ids=("chunk-a", "chunk-b"), corpus_with_marker={"chunk-a", "chunk-b"}
    )
    assert final["status"] == STATUS_SUCCESS
    assert final["fixture"]["selected_source_binding_verified"] is True
    assert final["privacy"]["targets"]["smoke_one_shot_logs"] == "SCANNED_AND_NOT_FOUND"
    assert final["resources"]["observation_verified"] is True


async def test_end_to_end_blocks_when_the_marker_is_only_on_an_unselected_chunk() -> None:
    """allowed member A has the marker but is not selected; selected B has none."""
    final = await _execute_then_finalize(selected_chunk_ids=("chunk-b",), corpus_with_marker={"chunk-a"})
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == FAILED_BY_SELECTED_SOURCE_SENTINEL_UNBOUND


async def test_end_to_end_blocks_when_one_selected_candidate_lacks_the_marker() -> None:
    final = await _execute_then_finalize(selected_chunk_ids=("chunk-a", "chunk-b"), corpus_with_marker={"chunk-a"})
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == FAILED_BY_SELECTED_SOURCE_SENTINEL_UNBOUND


async def test_end_to_end_blocks_when_the_query_leaked_into_the_one_shot_log() -> None:
    final = await _execute_then_finalize(
        selected_chunk_ids=("chunk-a",),
        corpus_with_marker={"chunk-a"},
        one_shot_log=f"submitting {QUERY_SENTINEL}",
    )
    assert final["status"] == STATUS_FAILED
    assert final["blocked_code"] == FAILED_BY_SENTINEL_FOUND
