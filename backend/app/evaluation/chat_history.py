from dataclasses import dataclass


@dataclass(frozen=True)
class ResponseExpectation:
    required_all: tuple[str, ...]
    required_any: tuple[tuple[str, ...], ...]
    forbidden: tuple[str, ...]


@dataclass(frozen=True)
class ResponseScore:
    passed: bool
    violations: tuple[str, ...]


def score_response(response: str, expectation: ResponseExpectation) -> ResponseScore:
    violations: list[str] = []
    if any(term not in response for term in expectation.required_all):
        violations.append("MISSING_REQUIRED_TERM")
    if any(not any(term in response for term in alternatives) for alternatives in expectation.required_any):
        violations.append("MISSING_REQUIRED_ALTERNATIVE")
    if any(term in response for term in expectation.forbidden):
        violations.append("FORBIDDEN_TERM_PRESENT")
    return ResponseScore(passed=not violations, violations=tuple(violations))
