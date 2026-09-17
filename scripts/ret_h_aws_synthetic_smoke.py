#!/usr/bin/env python3
"""EC2 + Docker Compose composition root for the Issue #178 RET-H synthetic smoke.

This script is the only place that joins the backend release-validation
orchestration with the ``ai_worker`` production retrieval runtime. It runs inside
the *app* image (which contains both ``app`` and ``ai_worker``) as a one-shot
Compose command; it never becomes a permanent service and never widens the
runtime worker's database privileges.

Intended EC2 invocation::

    docker compose --env-file envs/.prod.env \
      -f infra/docker/docker-compose.prod.yml \
      run --rm --no-deps -T fastapi \
      uv run --no-sync python -m scripts.ret_h_aws_synthetic_smoke \
        --mode aws-live \
        --git-commit-sha "$(git rev-parse HEAD)" \
        --fixture-manifest /app/ret-h-smoke-fixture.json \
        --output-path /app/ret-h-aws-synthetic-smoke.json

``--fixture-manifest`` points at the synthetic execution binding produced by the
separately credentialed one-shot bootstrap (see ``docs/testing/`` for #178). The
runtime identity used here can create the Retrieval Run but deliberately cannot
create the synthetic Knowledge Index; without a manifest the smoke reports
``AWS_SMOKE_NOT_EXECUTED`` rather than degrading.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.release_validation.ret_h_synthetic_smoke import (
    FASTAPI_CONTAINER_NAME,
    ONE_SHOT_CONTAINER_PREFIX,
    STATUS_FAILED,
    WORKER_CONTAINER_NAME,
    CheckResult,
    GateNegativeResult,
    LiveSmokeDependencies,
    ScanTarget,
    build_docker_log_reader,
    build_redis_stream_reader,
    finalize_smoke_artifact,
    parse_observation_document,
    parse_privacy_observation,
    run_ret_h_smoke,
    sentinels_from_fixture,
    write_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mode",
        choices=["aws-live-execute", "aws-live-finalize", "local-preflight"],
        default="local-preflight",
        help=(
            "aws-live-execute runs RET-H and stops before privacy certification; the host then "
            "scans and aws-live-finalize completes the artifact from that bound scan."
        ),
    )
    parser.add_argument("--git-commit-sha", default=None)
    parser.add_argument("--fixture-manifest", type=Path, default=None)
    parser.add_argument(
        "--privacy-observation-file",
        type=Path,
        default=None,
        help="Host-produced, run-bound POST-execution privacy scan (aws-live-finalize).",
    )
    parser.add_argument(
        "--interim-artifact",
        type=Path,
        default=None,
        help="Interim artifact emitted by aws-live-execute (aws-live-finalize).",
    )
    parser.add_argument(
        "--observation-file",
        type=Path,
        default=None,
        help="Host-produced deployment observation JSON (see docs/testing/ret-h-aws-synthetic-smoke-178.md).",
    )
    parser.add_argument("--output-path", type=Path, default=None)
    parser.add_argument(
        "--one-shot-container",
        default=ONE_SHOT_CONTAINER_PREFIX,
        help="Name given to the execute one-shot container, whose log is a required scan target.",
    )
    parser.add_argument("--redis-stream", default="oryak:jobs")
    parser.add_argument("--redis-dlq-stream", default="oryak:jobs:dead-letter")
    return parser


def _load_fixture(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def build_live_dependencies(
    fixture: dict[str, Any] | None,
    *,
    environment: Any,
    redis_stream: str,
    redis_dlq_stream: str,
    sentinels: Any = None,
    one_shot_container: str = ONE_SHOT_CONTAINER_PREFIX,
) -> LiveSmokeDependencies:
    """Assemble real production dependencies, or leave them absent (fail-closed)."""
    scan_targets = (
        ScanTarget("ai_worker_logs", build_docker_log_reader(WORKER_CONTAINER_NAME)),
        ScanTarget("fastapi_logs", build_docker_log_reader(FASTAPI_CONTAINER_NAME)),
        ScanTarget("redis_stream", build_redis_stream_reader(redis_stream)),
        ScanTarget("redis_dlq", build_redis_stream_reader(redis_dlq_stream)),
        # Scanned on the host after the run, once the one-shot container has exited but
        # before it is removed; see docs/testing/ret-h-aws-synthetic-smoke-178.md.
        ScanTarget("smoke_one_shot_logs", build_docker_log_reader(one_shot_container)),
        # This deployment has no separate quarantine storage for retrieval.
        ScanTarget("quarantine", reader=None, applicable=False),
    )

    if fixture is None or sentinels is None:
        return LiveSmokeDependencies(scan_targets=scan_targets)

    from uuid import UUID

    from ai_worker.core import get_config
    from ai_worker.tasks.evaluation.ret_h_smoke import (
        build_eligibility_verifier,
        build_receipt_verifier,
        build_run_store,
        build_search_port,
        build_session_factory,
        build_text_embedding_port,
        execute_ret_h_smoke_transaction,
        make_locator_mismatch_hit,
        make_stale_hit,
        verify_fixture_is_synthetic,
        verify_gate_fail_closed,
        verify_selected_candidates_carry_sentinel,
        verify_source_sentinel_indexed,
    )

    worker_config = get_config()
    _, session_factory = build_session_factory(worker_config.database_url)
    _, verification_session_factory = build_session_factory(worker_config.database_url)

    search_port = build_search_port(
        session_factory, content_sha256=str(fixture["search_adapter_ref"]["content_sha256"])
    )
    eligibility_verifier = build_eligibility_verifier(session_factory)
    run_store = build_run_store(session_factory)
    text_embedding_port = build_text_embedding_port(
        content_sha256=str(fixture["embedding_adapter_ref"]["content_sha256"]),
        environment=environment,
    )

    knowledge_index_id = UUID(str(fixture["knowledge_index_id"]))
    request = _build_hybrid_retrieve_request(fixture)

    captured: dict[str, Any] = {}

    async def _execution_fn(**kwargs: Any) -> Any:
        outcome = await execute_ret_h_smoke_transaction(**kwargs)
        captured["outcome"] = outcome
        return outcome

    def _negative(transform: Any) -> Any:
        async def _case() -> GateNegativeResult:
            outcome = captured.get("outcome")
            gate = getattr(outcome, "gate_outcome", None)
            selected = tuple(getattr(gate, "selected_hits", ()) or ())
            if not selected:
                return GateNegativeResult(False, False, message="no positive candidate to perturb")
            tampered = tuple(transform(hit) for hit in selected)
            fail_closed, message = await verify_gate_fail_closed(
                eligibility_verifier=eligibility_verifier,
                knowledge_index_id=knowledge_index_id,
                tampered_hits=tampered,
            )
            return GateNegativeResult(True, fail_closed, message=message)

        return _case

    allowed_snapshot_ids = tuple(UUID(str(v)) for v in fixture["allowed_source_snapshot_ids"])
    allowed_member_ids = tuple(UUID(str(v)) for v in fixture["allowed_source_snapshot_member_ids"])

    async def _source_sentinel_binding_case() -> CheckResult:
        bound, message = await verify_source_sentinel_indexed(
            session_factory=verification_session_factory,
            knowledge_index_id=knowledge_index_id,
            source_sentinel=sentinels.source_sentinel,
            allowed_source_snapshot_member_ids=allowed_member_ids,
        )
        return CheckResult(executed=True, passed=bound, message=message)

    async def _selected_source_binding_case(selected_chunk_ids: Any) -> CheckResult:
        bound, message = await verify_selected_candidates_carry_sentinel(
            session_factory=verification_session_factory,
            selected_chunk_ids=tuple(UUID(str(value)) for value in selected_chunk_ids),
            source_sentinel=sentinels.source_sentinel,
        )
        return CheckResult(executed=True, passed=bound, message=message)

    async def _fixture_authenticity_case() -> CheckResult:
        genuine, message = await verify_fixture_is_synthetic(
            session_factory=verification_session_factory,
            knowledge_index_id=knowledge_index_id,
            allowed_source_snapshot_ids=allowed_snapshot_ids,
        )
        return CheckResult(executed=True, passed=genuine, message=message)

    return LiveSmokeDependencies(
        session_factory=session_factory,
        verification_session_factory=verification_session_factory,
        search_port=search_port,
        text_embedding_port=text_embedding_port,
        run_store=run_store,
        eligibility_verifier=eligibility_verifier,
        hybrid_retrieve_request=request,
        execution_fn=_execution_fn,
        receipt_verifier=build_receipt_verifier(),
        source_sentinel_binding_case=_source_sentinel_binding_case,
        fixture_authenticity_case=_fixture_authenticity_case,
        selected_source_binding_case=_selected_source_binding_case,
        stale_case=_negative(make_stale_hit),
        locator_mismatch_case=_negative(make_locator_mismatch_hit),
        scan_targets=scan_targets,
        knowledge_index_ref=str(fixture.get("knowledge_index_ref")),
    )


def _build_hybrid_retrieve_request(fixture: dict[str, Any]) -> Any:
    """Reconstruct the pinned synthetic execution binding from the fixture manifest.

    The manifest is produced by the separately credentialed bootstrap; this script
    never invents identifiers and never writes Knowledge Index rows. The retrieval
    configuration is the merged production default with ``HYBRID_RRF`` - RET-H
    only - so no ranking or RRF parameter is redefined here.
    """
    import hashlib
    from uuid import UUID

    from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
    from ai_worker.tasks.rag.evidence_search import (
        EvidenceSearchExecutionBinding,
        EvidenceSearchRequest,
        QueryFingerprint,
        RetrievalExecutionMode,
        VersionedDenseSearchConfiguration,
        VersionedEvidenceRetrievalConfiguration,
        VersionedLexicalSearchConfiguration,
    )
    from ai_worker.tasks.rag.retrieval_runtime import HybridRetrieveRequest

    def _ref(key: str) -> ImmutableArtifactRef:
        raw = fixture[key]
        return ImmutableArtifactRef(
            artifact_code=str(raw["artifact_code"]),
            version=str(raw["version"]),
            content_sha256=str(raw["content_sha256"]),
        )

    lexical_config = VersionedLexicalSearchConfiguration(artifact_ref=_ref("lexical_config_ref"))
    dense_config = VersionedDenseSearchConfiguration(artifact_ref=_ref("dense_config_ref"))
    retrieval_config = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=_ref("retrieval_config_ref"),
        lexical_config=lexical_config,
        dense_config=dense_config,
        expected_query_embedding_adapter_ref=_ref("embedding_adapter_ref"),
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )

    binding = EvidenceSearchExecutionBinding(
        filter_snapshot_ref=_ref("filter_snapshot_ref"),
        evidence_index_ref=_ref("evidence_index_ref"),
        knowledge_index_id=UUID(str(fixture["knowledge_index_id"])),
        allowed_source_snapshot_ids=tuple(UUID(str(v)) for v in fixture["allowed_source_snapshot_ids"]),
        allowed_source_snapshot_member_ids=tuple(UUID(str(v)) for v in fixture["allowed_source_snapshot_member_ids"]),
        retrieval_config=retrieval_config,
    )

    query = str(fixture["synthetic_query"]).strip()
    search_request = EvidenceSearchRequest(
        normalized_query=SensitiveText(query),
        query_fingerprint=QueryFingerprint(
            algorithm="sha256",
            key_version="v1",
            digest=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        ),
        execution_binding=binding,
        query_embedding_receipt=None,
    )

    return HybridRetrieveRequest(
        job_id=UUID(str(fixture["job_id"])),
        execution_context_id=UUID(str(fixture["execution_context_id"])),
        prescription_version_id=UUID(str(fixture["prescription_version_id"])),
        runtime_release_bundle_id=UUID(str(fixture["runtime_release_bundle_id"])),
        runtime_release_bundle_manifest_hash=str(fixture["runtime_release_bundle_manifest_hash"]),
        runtime_execution_manifest_id=UUID(str(fixture["runtime_execution_manifest_id"])),
        runtime_execution_manifest_hash=str(fixture["runtime_execution_manifest_hash"]),
        runtime_guard_decision_ref=str(fixture["runtime_guard_decision_ref"]),
        search_request=search_request,
        source_manifest_hash=str(fixture["source_manifest_hash"]),
    )


def _finalize(args: Any) -> int:
    """Complete an interim artifact from the bound post-execution host scan."""
    if not args.interim_artifact or not args.interim_artifact.is_file():
        print("aws-live-finalize requires --interim-artifact", file=sys.stderr)
        return 1
    if not args.privacy_observation_file or not args.privacy_observation_file.is_file():
        print("aws-live-finalize requires --privacy-observation-file", file=sys.stderr)
        return 1

    fixture = _load_fixture(args.fixture_manifest)
    sentinels = sentinels_from_fixture(fixture) if fixture else None
    if sentinels is None:
        print("aws-live-finalize requires the approved fixture manifest", file=sys.stderr)
        return 1

    document = json.loads(args.interim_artifact.read_text(encoding="utf-8"))
    try:
        observation = parse_privacy_observation(json.loads(args.privacy_observation_file.read_text(encoding="utf-8")))
    except Exception as error:  # noqa: BLE001 - a bad document must block, not crash
        print(f"privacy observation unusable: {type(error).__name__}", file=sys.stderr)
        return 1

    final = finalize_smoke_artifact(document, privacy_observation=observation, sentinels=sentinels)
    payload = json.dumps(final, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output_path:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(payload + "\n", encoding="utf-8")
        print(f"{final['status']} -> {args.output_path}")
    else:
        print(payload)
    return 1 if final["status"] == STATUS_FAILED else 0


def main(argv: list[str] | None = None) -> int:
    import os

    args = build_parser().parse_args(argv)
    if args.mode == "aws-live-finalize":
        return _finalize(args)

    live = args.mode == "aws-live-execute"
    fixture = _load_fixture(args.fixture_manifest) if live else None

    # Sentinels come from the approved fixture. This runner never mints its own: a
    # freshly generated string is not in the submitted query or the indexed Source, so
    # scanning for it would pass vacuously.
    sentinels = sentinels_from_fixture(fixture) if fixture else None
    submitted_query = str(fixture.get("synthetic_query", "")) if fixture else None
    approved_query_sha256 = str(fixture.get("synthetic_query_sha256", "")) if fixture else None

    # Deployment identity and resource sampling are produced on the EC2 host, which
    # already has the Docker CLI. This container is never given a Docker daemon socket.
    observation = None
    if live and args.observation_file and args.observation_file.is_file():
        try:
            observation = parse_observation_document(json.loads(args.observation_file.read_text(encoding="utf-8")))
        except Exception as error:  # noqa: BLE001 - a bad document must block, not crash
            print(f"observation document unusable: {type(error).__name__}", file=sys.stderr)

    dependencies = build_live_dependencies(
        fixture,
        environment=os.environ,
        redis_stream=args.redis_stream,
        redis_dlq_stream=args.redis_dlq_stream,
        sentinels=sentinels,
        one_shot_container=args.one_shot_container,
    )

    receipt = asyncio.run(
        run_ret_h_smoke(
            mode="aws-live" if live else args.mode,
            environment=os.environ,
            git_commit_sha=args.git_commit_sha,
            dependencies=dependencies,
            worker_facts=observation.worker if observation else None,
            worker_image_digest=observation.image_digest if observation else None,
            resource_observation=observation.resources if observation else None,
            sentinels=sentinels,
            submitted_query=submitted_query,
            approved_query_sha256=approved_query_sha256,
            # The execute phase never certifies privacy; only a bound post-execution
            # host scan can do that, in aws-live-finalize.
            defer_privacy=live,
        )
    )

    payload = write_artifact(receipt, args.output_path)
    if args.output_path is None:
        print(payload)
    else:
        print(f"{receipt.status} -> {args.output_path}")
    return 1 if receipt.status == STATUS_FAILED else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
