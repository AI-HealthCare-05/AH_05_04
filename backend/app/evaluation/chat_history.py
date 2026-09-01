import copy
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.services.chat_ai.client import ChatProvider
from app.services.chat_ai.generator import ChatGenerator
from app.services.chat_ai.prompt import PROMPT_VERSION
from app.services.chat_ai.schemas import (
    ChatGenerationInput,
    ChatHistoryItem,
    ChatMedicationInput,
    ProviderChatResponse,
)


@dataclass(frozen=True)
class ResponseExpectation:
    required_all: tuple[str, ...]
    required_any: tuple[tuple[str, ...], ...]
    forbidden: tuple[str, ...]


@dataclass(frozen=True)
class ResponseScore:
    passed: bool
    violations: tuple[str, ...]


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    baseline: ResponseScore
    history: ResponseScore
    baseline_identified: bool | None
    history_identified: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "baseline": {"passed": self.baseline.passed, "violations": list(self.baseline.violations)},
            "history": {"passed": self.history.passed, "violations": list(self.history.violations)},
            "baseline_identified": self.baseline_identified,
            "history_identified": self.history_identified,
        }


@dataclass(frozen=True)
class EvaluationReport:
    dataset_id: str
    metrics: dict[str, int | str]
    cases: tuple[CaseEvaluation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "run_mode": "DETERMINISTIC_REPLAY",
            "provider_evaluation": {
                "status": "NOT_RUN",
                "reason": "Actual OpenAI evaluation requires explicit opt-in and was not requested.",
            },
            "metrics": self.metrics,
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class ProviderCall:
    instructions: str
    input_json: str


class ReplayProvider:
    def __init__(self, outputs: tuple[str, ...]) -> None:
        self._outputs = iter(outputs)
        self.calls: list[ProviderCall] = []

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderChatResponse:
        del max_output_tokens
        self.calls.append(ProviderCall(instructions=instructions, input_json=input_json))
        return ProviderChatResponse(content=next(self._outputs), model_name=f"deterministic-{model}")


class RecordingProvider:
    def __init__(self, provider: ChatProvider) -> None:
        self._provider = provider
        self.calls: list[ProviderCall] = []

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderChatResponse:
        self.calls.append(ProviderCall(instructions=instructions, input_json=input_json))
        return await self._provider.generate(
            model=model,
            instructions=instructions,
            input_json=input_json,
            max_output_tokens=max_output_tokens,
        )


@dataclass(frozen=True)
class ExecutionReport:
    evaluation: EvaluationReport
    model_settings: dict[str, object]
    observations: dict[str, object]
    pii_sentinel_audit: dict[str, int | str]
    run_mode: str
    provider_evaluation: dict[str, int | str]

    def to_dict(self) -> dict[str, object]:
        report = self.evaluation.to_dict()
        report.update(
            {
                "prompt_version": PROMPT_VERSION,
                "run_mode": self.run_mode,
                "provider_evaluation": self.provider_evaluation,
                "model_settings": self.model_settings,
                "observations": self.observations,
                "pii_sentinel_audit": self.pii_sentinel_audit,
            }
        )
        return report


class LiveEvaluationConfigurationError(ValueError):
    pass


class EvaluationExecutionError(RuntimeError):
    pass


def validate_live_environment(environment: Mapping[str, str]) -> None:
    api_key = environment.get("OPENAI_API_KEY", "")
    if (
        environment.get("RUN_OPENAI_CHAT_HISTORY_EVAL") != "1"
        or environment.get("ENV") != "local"
        or not api_key
        or api_key == "sk-not-configured"
    ):
        raise LiveEvaluationConfigurationError("Live Chat history evaluation is not enabled")


def score_response(response: str, expectation: ResponseExpectation) -> ResponseScore:
    violations: list[str] = []
    if any(term not in response for term in expectation.required_all):
        violations.append("MISSING_REQUIRED_TERM")
    if any(not any(term in response for term in alternatives) for alternatives in expectation.required_any):
        violations.append("MISSING_REQUIRED_ALTERNATIVE")
    if any(term in response for term in expectation.forbidden):
        violations.append("FORBIDDEN_TERM_PRESENT")
    return ResponseScore(passed=not violations, violations=tuple(violations))


def _parse_expectation(raw: dict[str, Any]) -> ResponseExpectation:
    return ResponseExpectation(
        required_all=tuple(raw["required_all"]),
        required_any=tuple(tuple(group) for group in raw["required_any"]),
        forbidden=tuple(raw["forbidden"]),
    )


def evaluate_replay_dataset(dataset: dict[str, Any]) -> EvaluationReport:
    cases: list[CaseEvaluation] = []
    for raw_case in dataset["cases"]:
        baseline_output = raw_case["replay_outputs"]["baseline"]
        history_output = raw_case["replay_outputs"]["history"]
        markers = tuple(raw_case.get("identification_markers", ()))
        cases.append(
            CaseEvaluation(
                case_id=raw_case["case_id"],
                baseline=score_response(
                    baseline_output,
                    _parse_expectation(raw_case["expected"]["baseline"]),
                ),
                history=score_response(
                    history_output,
                    _parse_expectation(raw_case["expected"]["history"]),
                ),
                baseline_identified=(all(marker in baseline_output for marker in markers) if markers else None),
                history_identified=(all(marker in history_output for marker in markers) if markers else None),
            )
        )

    followup_cases = [case for case in cases if case.baseline_identified is not None]
    single_turn_ids = {
        raw_case["case_id"]
        for raw_case in dataset["cases"]
        if "single_turn_regression" in raw_case.get("metric_tags", ())
    }
    single_turn_cases = [case for case in cases if case.case_id in single_turn_ids]
    safety_ids = {raw_case["case_id"] for raw_case in dataset["cases"] if "safety" in raw_case.get("metric_tags", ())}
    safety_cases = [case for case in cases if case.case_id in safety_ids]
    metrics: dict[str, int | str] = {
        "case_count": len(cases),
        "baseline_pass_count": sum(case.baseline.passed for case in cases),
        "history_pass_count": sum(case.history.passed for case in cases),
        "followup_case_count": len(followup_cases),
        "baseline_identification_count": sum(case.baseline_identified is True for case in followup_cases),
        "history_identification_count": sum(case.history_identified is True for case in followup_cases),
        "single_turn_baseline_pass_count": sum(case.baseline.passed for case in single_turn_cases),
        "single_turn_history_pass_count": sum(case.history.passed for case in single_turn_cases),
        "safety_violation_count": sum(
            len(case.baseline.violations) + len(case.history.violations) for case in safety_cases
        ),
        "threshold_status": "NOT_APPLICABLE_SAMPLE_LT_30",
    }
    return EvaluationReport(dataset_id=dataset["dataset_id"], metrics=metrics, cases=tuple(cases))


def _p95_milliseconds(durations: list[float]) -> float:
    ordered = sorted(durations)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index] * 1000


def _generation_input(raw_case: dict[str, Any], *, include_history: bool) -> ChatGenerationInput:
    history = [ChatHistoryItem(**item) for item in raw_case["history"]] if include_history else []
    medications = [ChatMedicationInput(**item) for item in raw_case["medications"]]
    return ChatGenerationInput(question=raw_case["question"], history=history, medications=medications)


def _audit_pii_sentinels(
    *,
    call: ProviderCall,
    response: str,
    sentinels: tuple[str, ...],
) -> tuple[int, int]:
    payload = json.loads(call.input_json)
    history_json = json.dumps(payload["history"], ensure_ascii=False)
    allowed = sum(history_json.count(sentinel) for sentinel in sentinels)
    forbidden = sum(call.input_json.count(sentinel) for sentinel in sentinels) - allowed
    forbidden += sum(call.instructions.count(sentinel) + response.count(sentinel) for sentinel in sentinels)
    return allowed, forbidden


async def _timed_generate(
    generator: ChatGenerator,
    chat_input: ChatGenerationInput,
    *,
    clock: Callable[[], float],
) -> tuple[str, float]:
    started_at = clock()
    result = await generator.generate(chat_input)
    return result.content, clock() - started_at


async def _run_evaluation(
    dataset: dict[str, Any],
    *,
    provider_factory: Callable[[tuple[str, ...]], ReplayProvider | RecordingProvider],
    clock: Callable[[], float],
    run_mode: str,
    provider_evaluation: dict[str, int | str],
) -> ExecutionReport:
    model_settings = dict(dataset["model_settings"])
    evaluated_dataset = copy.deepcopy(dataset)
    baseline_durations: list[float] = []
    history_durations: list[float] = []
    sentinel_case_count = 0
    allowed_sentinel_occurrences = 0
    forbidden_sentinel_replications = 0

    for raw_case, evaluated_case in zip(dataset["cases"], evaluated_dataset["cases"], strict=True):
        outputs = raw_case["replay_outputs"]
        provider = provider_factory((outputs["baseline"], outputs["history"]))
        generator = ChatGenerator(
            provider=provider,
            model=str(model_settings["model"]),
            timeout_seconds=float(model_settings["timeout_seconds"]),
        )
        baseline_output, baseline_duration = await _timed_generate(
            generator,
            _generation_input(raw_case, include_history=False),
            clock=clock,
        )
        history_output, history_duration = await _timed_generate(
            generator,
            _generation_input(raw_case, include_history=True),
            clock=clock,
        )
        baseline_durations.append(baseline_duration)
        history_durations.append(history_duration)
        evaluated_case["replay_outputs"] = {"baseline": baseline_output, "history": history_output}

        sentinels = tuple(raw_case.get("pii_sentinels", ()))
        if sentinels:
            sentinel_case_count += 1
            allowed, forbidden = _audit_pii_sentinels(
                call=provider.calls[1],
                response=history_output,
                sentinels=sentinels,
            )
            allowed_sentinel_occurrences += allowed
            forbidden_sentinel_replications += forbidden

    fixture = dataset["max_history_fixture"]
    max_history = [
        ChatHistoryItem(
            question="가" * int(fixture["question_length"]),
            answer="나" * int(fixture["answer_length"]),
        )
        for _ in range(int(fixture["pair_count"]))
    ]
    boundary_provider = provider_factory(("최대 history 합성 검증 답변입니다.",))
    boundary_generator = ChatGenerator(
        provider=boundary_provider,
        model=str(model_settings["model"]),
        timeout_seconds=float(model_settings["timeout_seconds"]),
    )
    _, boundary_duration = await _timed_generate(
        boundary_generator,
        ChatGenerationInput(
            question="최대 history 경계를 검증해 주세요.",
            history=max_history,
            medications=[ChatMedicationInput(medication_name="합성의약품 에이")],
        ),
        clock=clock,
    )

    observations: dict[str, object] = {
        "baseline_p95_ms": _p95_milliseconds(baseline_durations),
        "history_p95_ms": _p95_milliseconds(history_durations),
        "max_history_p95_ms": boundary_duration * 1000,
        "max_history_characters": sum(len(item.question) + len(item.answer) for item in max_history),
        "max_payload_bytes": len(boundary_provider.calls[0].input_json.encode("utf-8")),
        "token_count": {"status": "NOT_RUN", "reason": "No approved provider tokenizer is configured."},
    }
    pii_sentinel_audit: dict[str, int | str] = {
        "case_count": sentinel_case_count,
        "allowed_history_occurrence_count": allowed_sentinel_occurrences,
        "forbidden_replication_count": forbidden_sentinel_replications,
        "trace_status": "NOT_APPLICABLE_NO_TRACE_PIPELINE",
    }
    return ExecutionReport(
        evaluation=evaluate_replay_dataset(evaluated_dataset),
        model_settings=model_settings,
        observations=observations,
        pii_sentinel_audit=pii_sentinel_audit,
        run_mode=run_mode,
        provider_evaluation=provider_evaluation,
    )


async def run_deterministic_evaluation(
    dataset: dict[str, Any],
    *,
    clock: Callable[[], float],
) -> ExecutionReport:
    return await _run_evaluation(
        dataset,
        provider_factory=ReplayProvider,
        clock=clock,
        run_mode="DETERMINISTIC_REPLAY",
        provider_evaluation={
            "status": "NOT_RUN",
            "reason": "Actual OpenAI evaluation requires explicit opt-in and was not requested.",
        },
    )


async def run_live_evaluation(
    dataset: dict[str, Any],
    *,
    provider: ChatProvider,
    clock: Callable[[], float],
) -> ExecutionReport:
    response_count = len(dataset["cases"]) * 2 + 1
    try:
        return await _run_evaluation(
            dataset,
            provider_factory=lambda outputs: RecordingProvider(provider),
            clock=clock,
            run_mode="LIVE_PROVIDER",
            provider_evaluation={"status": "RUN", "response_count": response_count},
        )
    except Exception:
        raise EvaluationExecutionError("Chat history evaluation failed") from None
