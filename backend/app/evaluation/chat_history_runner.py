import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from app.evaluation.chat_history import (
    EvaluationExecutionError,
    LiveEvaluationConfigurationError,
    run_deterministic_evaluation,
    run_live_evaluation,
    validate_live_environment,
)
from app.services.chat_ai.client import OpenAIResponsesClient

_REPOSITORY_ROOT = Path(__file__).parents[3]
_DEFAULT_DATASET_PATH = _REPOSITORY_ROOT / "evals" / "generation" / "chat-v2-history-eval-v1.json"


@dataclass(frozen=True)
class RunnerArguments:
    mode: str
    dataset_path: Path
    output_path: Path


async def execute(
    arguments: RunnerArguments,
    *,
    environment: Mapping[str, str],
    clock: Callable[[], float] = perf_counter,
) -> int:
    dataset = json.loads(arguments.dataset_path.read_text(encoding="utf-8"))
    if arguments.mode == "deterministic":
        report = await run_deterministic_evaluation(dataset, clock=clock)
    elif arguments.mode == "live":
        validate_live_environment(environment)
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=environment["OPENAI_API_KEY"],
            timeout=float(dataset["model_settings"]["timeout_seconds"]),
        )
        try:
            report = await run_live_evaluation(
                dataset,
                provider=OpenAIResponsesClient(client),
                clock=clock,
            )
        finally:
            await client.close()
    else:
        raise ValueError("mode must be deterministic or live")

    arguments.output_path.parent.mkdir(parents=True, exist_ok=True)
    arguments.output_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


def _parse_arguments(argv: list[str] | None = None) -> RunnerArguments:
    parser = argparse.ArgumentParser(description="Run the synthetic Chat history evaluation")
    parser.add_argument("--mode", choices=("deterministic", "live"), default="deterministic")
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parsed = parser.parse_args(argv)
    return RunnerArguments(mode=parsed.mode, dataset_path=parsed.dataset, output_path=parsed.output)


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(execute(_parse_arguments(argv), environment=os.environ))
    except (LiveEvaluationConfigurationError, EvaluationExecutionError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
