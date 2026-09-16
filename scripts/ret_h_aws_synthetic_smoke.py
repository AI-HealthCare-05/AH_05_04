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
    STATUS_FAILED,
    WORKER_CONTAINER_NAME,
    GateNegativeResult,
    LiveSmokeDependencies,
    ScanTarget,
    build_docker_log_reader,
    build_redis_stream_reader,
    collect_resource_observation,
    collect_worker_facts,
    generate_sentinels,
    run_ret_h_smoke,
    write_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["aws-live", "local-preflight"], default="local-preflight")
    parser.add_argument("--git-commit-sha", default=None)
    parser.add_argument("--fixture-manifest", type=Path, default=None)
    parser.add_argument("--output-path", type=Path, default=None)
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
) -> LiveSmokeDependencies:
    """Assemble real production dependencies, or leave them absent (fail-closed)."""
    scan_targets = (
        ScanTarget("ai_worker_logs", build_docker_log_reader(WORKER_CONTAINER_NAME)),
        ScanTarget("fastapi_logs", build_docker_log_reader(FASTAPI_CONTAINER_NAME)),
        ScanTarget("redis_stream", build_redis_stream_reader(redis_stream)),
        ScanTarget("redis_dlq", build_redis_stream_reader(redis_dlq_stream)),
        # This deployment has no separate quarantine storage for retrieval.
        ScanTarget("quarantine", reader=None, applicable=False),
    )

    if fixture is None:
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
        verify_gate_fail_closed,
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


def main(argv: list[str] | None = None) -> int:
    import os

    args = build_parser().parse_args(argv)
    sentinels = generate_sentinels()

    worker_facts = None
    worker_digest = None
    resources = None
    if args.mode == "aws-live":
        try:
            worker_facts, worker_digest = collect_worker_facts()
            resources = collect_resource_observation()
        except Exception as error:  # noqa: BLE001 - observation failure must block, not crash
            print(f"deployment observation unavailable: {type(error).__name__}", file=sys.stderr)

    fixture = _load_fixture(args.fixture_manifest) if args.mode == "aws-live" else None
    dependencies = build_live_dependencies(
        fixture,
        environment=os.environ,
        redis_stream=args.redis_stream,
        redis_dlq_stream=args.redis_dlq_stream,
    )

    receipt = asyncio.run(
        run_ret_h_smoke(
            mode=args.mode,
            environment=os.environ,
            git_commit_sha=args.git_commit_sha,
            dependencies=dependencies,
            worker_facts=worker_facts,
            worker_image_digest=worker_digest,
            resource_observation=resources,
            sentinels=sentinels,
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
