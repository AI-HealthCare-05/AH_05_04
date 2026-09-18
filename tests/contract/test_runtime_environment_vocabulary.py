"""Contract tests for canonical Runtime Environment vocabulary (PD-799-20260918, Issue #810)."""

from ai_worker.tasks.rag.citation_authorization import RuntimeEnvironment
from rag_runtime.runtime_environment import RuntimeEnvironmentCode


def test_canonical_runtime_environment_vocabulary_members() -> None:
    expected = {"LOCAL", "TEST", "CLOSED_DEMO", "PRODUCTION"}
    assert {member.value for member in RuntimeEnvironmentCode} == expected
    assert len(RuntimeEnvironmentCode) == 4


def test_runtime_environment_vocabulary_equals_citation_authorization_vocabulary() -> None:
    assert {member.value for member in RuntimeEnvironmentCode} == {member.value for member in RuntimeEnvironment}


def test_runtime_environment_vocabulary_exact_values() -> None:
    assert RuntimeEnvironmentCode.LOCAL.value == "LOCAL"
    assert RuntimeEnvironmentCode.TEST.value == "TEST"
    assert RuntimeEnvironmentCode.CLOSED_DEMO.value == "CLOSED_DEMO"
    assert RuntimeEnvironmentCode.PRODUCTION.value == "PRODUCTION"


def test_runtime_environment_vocabulary_fails_closed_on_invalid() -> None:
    invalid_cases = [
        "local",
        "test",
        "production",
        "closed_demo",
        "STAGING",
        "DEV",
        "UNKNOWN",
        " LOCAL ",
        "TEST ",
        " TEST",
        "",
    ]
    for case in invalid_cases:
        try:
            RuntimeEnvironmentCode(case)
        except ValueError:
            pass
        else:
            raise AssertionError(f"RuntimeEnvironmentCode unexpectedly accepted: {case!r}")
