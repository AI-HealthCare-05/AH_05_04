import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from app.evaluation.chat_blind_ab import (
    artifact_json_bytes,
    build_judgment_template,
    load_blind_ab_experiment,
    normalized_sha256,
    run_blind_ab_evaluation,
    unblind_judgments,
)
from app.evaluation.chat_history import EvaluationExecutionError, LiveEvaluationConfigurationError
from app.services.chat_ai.client import OpenAIResponsesClient

_REPOSITORY_ROOT = Path(__file__).parents[3]
_DEFAULT_CONFIG_PATH = _REPOSITORY_ROOT / "evals" / "generation" / "chat-conversation-quality-blind-ab-v1.json"
_ALLOWED_CONFIG_SHA256_BY_PATH = {
    _DEFAULT_CONFIG_PATH.resolve(): "bc61b2511945288b5b46d35c2cdfa63bc38601c3965fee2a4ce31da0468f2205",
    (_REPOSITORY_ROOT / "evals" / "generation" / "chat-feedback-gold-prompt-comparison-v1.json").resolve(): (
        "1bc9cd8901d31d588215383408d9e696ff092381c72c8debafa799728fd8962a"
    ),
}
_API_KEY_PLACEHOLDERS = frozenset(
    {
        "",
        "placeholder",
        "change-me",
        "changeme",
        "not-configured",
        "sk-not-configured",
        "your-api-key",
        "your-secret-key",
        "replace-with-openai-api-key",
        "replace-with-production-openai-api-key",
    }
)


@dataclass(frozen=True)
class RunArguments:
    config_path: Path
    review_packet_path: Path
    judgment_template_path: Path
    assignment_path: Path


@dataclass(frozen=True)
class UnblindArguments:
    assignment_path: Path
    judgments_path: Path
    output_path: Path


def validate_blind_ab_environment(environment: Mapping[str, str]) -> None:
    api_key = environment.get("OPENAI_API_KEY", "").strip()
    if (
        environment.get("RUN_OPENAI_CHAT_BLIND_AB_EVAL") != "1"
        or environment.get("ENV") != "local"
        or api_key.casefold() in _API_KEY_PLACEHOLDERS
    ):
        raise LiveEvaluationConfigurationError("Live Chat blind A/B evaluation is not enabled")


def _validate_canonical_config(config_path: Path) -> None:
    resolved_path = config_path.resolve()
    allowed_hash = _ALLOWED_CONFIG_SHA256_BY_PATH.get(resolved_path)
    if allowed_hash is None or normalized_sha256(config_path.read_bytes()) != allowed_hash:
        raise LiveEvaluationConfigurationError("Chat blind A/B experiment config is not allowed")


async def execute_run(arguments: RunArguments, *, environment: Mapping[str, str]) -> int:
    validate_blind_ab_environment(environment)
    _validate_canonical_config(arguments.config_path)
    experiment, dataset = load_blind_ab_experiment(arguments.config_path)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=environment["OPENAI_API_KEY"].strip(),
        timeout=experiment.timeout_seconds,
    )
    try:
        review_packet, assignment_artifact = await run_blind_ab_evaluation(
            experiment,
            dataset,
            provider_factory=lambda _variant: OpenAIResponsesClient(
                client,
                observability_disabled=True,
            ),
            clock=perf_counter,
        )
    finally:
        await client.close()

    arguments.review_packet_path.parent.mkdir(parents=True, exist_ok=True)
    arguments.judgment_template_path.parent.mkdir(parents=True, exist_ok=True)
    arguments.assignment_path.parent.mkdir(parents=True, exist_ok=True)
    arguments.review_packet_path.write_bytes(artifact_json_bytes(review_packet))
    arguments.judgment_template_path.write_bytes(
        artifact_json_bytes(
            build_judgment_template(
                review_packet,
                str(assignment_artifact["review_packet_sha256"]),
            )
        )
    )
    arguments.assignment_path.touch(mode=0o600, exist_ok=True)
    arguments.assignment_path.chmod(0o600)
    arguments.assignment_path.write_bytes(artifact_json_bytes(assignment_artifact))
    variants = assignment_artifact["variants"]
    if not isinstance(variants, list):
        raise ValueError("Blind A/B variant results are invalid")
    return 0 if all(variant["passed"] is True for variant in variants) else 1


def execute_unblind(arguments: UnblindArguments) -> int:
    assignment_artifact = json.loads(arguments.assignment_path.read_bytes())
    judgments = json.loads(arguments.judgments_path.read_bytes())
    result = unblind_judgments(assignment_artifact, judgments)
    arguments.output_path.parent.mkdir(parents=True, exist_ok=True)
    arguments.output_path.write_bytes(artifact_json_bytes(result))
    return 0


def _parse_arguments(argv: list[str] | None = None) -> RunArguments | UnblindArguments:
    parser = argparse.ArgumentParser(description="Run or unblind the synthetic Chat prompt A/B evaluation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG_PATH)
    run_parser.add_argument("--review-packet", type=Path, required=True)
    run_parser.add_argument("--judgment-template", type=Path, required=True)
    run_parser.add_argument("--assignment", type=Path, required=True)

    unblind_parser = subparsers.add_parser("unblind")
    unblind_parser.add_argument("--assignment", type=Path, required=True)
    unblind_parser.add_argument("--judgments", type=Path, required=True)
    unblind_parser.add_argument("--output", type=Path, required=True)

    parsed = parser.parse_args(argv)
    if parsed.command == "run":
        return RunArguments(
            config_path=parsed.config,
            review_packet_path=parsed.review_packet,
            judgment_template_path=parsed.judgment_template,
            assignment_path=parsed.assignment,
        )
    return UnblindArguments(
        assignment_path=parsed.assignment,
        judgments_path=parsed.judgments,
        output_path=parsed.output,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        if isinstance(arguments, RunArguments):
            return asyncio.run(execute_run(arguments, environment=os.environ))
        return execute_unblind(arguments)
    except (LiveEvaluationConfigurationError, EvaluationExecutionError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
