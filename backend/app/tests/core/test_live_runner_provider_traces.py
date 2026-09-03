from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.release_validation.ai_one_cycle_smoke import (
    HttpFlowError,
    NetworkOneCycleRunner,
    RunStateStore,
    _apply_local_live_evidence_contract,
    _failure,
    _ocr_database_evidence,
)


class FakeClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.headers: list[dict[str, str]] = []

    async def request(self, _method: str, _path: str, **kwargs: Any) -> httpx.Response:
        self.headers.append(dict(kwargs["headers"]))
        return self.responses.pop(0)


def _response(status: int, body: dict[str, object], trace_id: str | None) -> httpx.Response:
    headers = {"Cache-Control": "no-store"}
    if trace_id is not None:
        headers["X-Trace-Id"] = trace_id
    return httpx.Response(status, headers=headers, json=body)


def _runner(
    tmp_path: Path, *, mode: str = "local-live-full", ocr_structuring_expected: bool = True
) -> NetworkOneCycleRunner:
    run_id = "61a10000-0000-4000-8000-000000000003"
    state = RunStateStore.create(
        tmp_path,
        run_id,
        {"run_id": run_id, "mode": mode, "ids": {}},
    )
    return NetworkOneCycleRunner(
        base_url="http://127.0.0.1:8000/api/v1",
        state=state,
        read_timeout_seconds=5,
        ocr_structuring_expected=ocr_structuring_expected,
    )


async def test_local_live_runner_preserves_validation_header_after_login_and_records_provider_traces(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    traces = {
        "auth": "1" * 32,
        "ocr": "2" * 32,
        "guide": "3" * 32,
        "chat": "4" * 32,
    }
    client = FakeClient(
        [
            _response(200, {"access_token": "synthetic-token"}, traces["auth"]),
            _response(202, {"data": {"job_id": "job"}}, traces["ocr"]),
            _response(201, {"data": {}}, traces["guide"]),
            _response(201, {"data": {}}, traces["chat"]),
        ]
    )
    runner._client = client  # type: ignore[assignment]

    await runner._login(email="synthetic@example.invalid", password="synthetic-password")
    await runner._request("OCR_REQUEST", "POST", "/documents/doc/ocr-jobs", expected_status=202)
    await runner._request("GUIDE_GENERATION_PROCESSING", "POST", "/guides", expected_status=201)
    await runner._request("CHAT_GENERATION_PROCESSING", "POST", "/chat", expected_status=201)

    assert all(headers["X-Validation-Run-Id"] == "61a10000-0000-4000-8000-000000000003" for headers in client.headers)
    assert client.headers[0].get("Authorization") is None
    assert all(headers["Authorization"] == "Bearer synthetic-token" for headers in client.headers[1:])
    assert runner.provider_traces == {
        "prescription_recognition": {"status": "EXPECTED", "trace_id": traces["ocr"]},
        "ocr_structuring": {"status": "EXPECTED", "trace_id": traces["ocr"]},
        "guide_generation": {"status": "EXPECTED", "trace_id": traces["guide"]},
        "chat_generation": {"status": "EXPECTED", "trace_id": traces["chat"]},
    }


async def test_local_live_runner_marks_disabled_ocr_structuring_as_skipped(tmp_path: Path) -> None:
    runner = _runner(tmp_path, ocr_structuring_expected=False)
    runner._client = FakeClient([_response(202, {"data": {}}, "2" * 32)])  # type: ignore[assignment]

    await runner._request("OCR_REQUEST", "POST", "/documents/doc/ocr-jobs", expected_status=202)

    assert runner.provider_traces["ocr_structuring"] == {
        "status": "SKIPPED",
        "reason": "OCR_STRUCTURE_LLM_DISABLED",
        "trace_id": None,
    }


@pytest.mark.parametrize(
    ("header_trace", "body_trace", "expected_code"),
    [
        (None, None, "TRACE_ID_MISSING"),
        ("5" * 32, "6" * 32, "TRACE_ID_MISMATCH"),
        ("not-hex", None, "TRACE_ID_INVALID"),
    ],
)
async def test_local_live_runner_rejects_missing_invalid_or_mismatched_trace(
    tmp_path: Path,
    header_trace: str | None,
    body_trace: str | None,
    expected_code: str,
) -> None:
    runner = _runner(tmp_path)
    body: dict[str, object] = {"code": "HTTP_ERROR"}
    if body_trace is not None:
        body["trace_id"] = body_trace
    runner._client = FakeClient([_response(500, body, header_trace)])  # type: ignore[assignment]

    with pytest.raises(HttpFlowError) as exc_info:
        await runner._request("GUIDE_GENERATION_PROCESSING", "POST", "/guides", expected_status=201)

    assert exc_info.value.evidence["api_code"] == expected_code


@pytest.mark.parametrize(
    ("reason", "expected_reason"),
    [
        ("DEADLINE_EXCEEDED", "DEADLINE_EXCEEDED"),
        ("PROVIDER_TIMEOUT", "PROVIDER_TIMEOUT"),
        ("SENSITIVE_PROVIDER_DETAIL", None),
        (123, None),
    ],
)
async def test_local_live_runner_copies_only_allowlisted_api_failure_reason(
    tmp_path: Path,
    reason: object,
    expected_reason: str | None,
) -> None:
    runner = _runner(tmp_path)
    runner._client = FakeClient(  # type: ignore[assignment]
        [
            _response(
                503,
                {
                    "code": "OCR_PROVIDER_TIMEOUT",
                    "trace_id": "7" * 32,
                    "details": {"reason": reason, "secret": "MUST_NOT_ESCAPE"},
                },
                "7" * 32,
            )
        ]
    )

    with pytest.raises(HttpFlowError) as exc_info:
        await runner._request("OCR_REQUEST", "POST", "/ocr", expected_status=202)

    if expected_reason is None:
        assert "api_reason" not in exc_info.value.evidence
    else:
        assert exc_info.value.evidence["api_reason"] == expected_reason
    assert "MUST_NOT_ESCAPE" not in str(exc_info.value.evidence)


async def test_staging_runner_does_not_send_local_validation_header_or_require_trace(tmp_path: Path) -> None:
    runner = _runner(tmp_path, mode="staging-live")
    client = FakeClient([_response(200, {"data": {}}, None)])
    runner._client = client  # type: ignore[assignment]

    await runner._request("STAGING", "GET", "/resource", expected_status=200)

    assert "X-Validation-Run-Id" not in client.headers[0]
    assert runner.provider_traces == {}


def test_local_live_result_requires_manual_provider_log_review_without_claiming_full_evidence() -> None:
    result = {
        "execution": "PASS",
        "provider_traces": {"guide_generation": {"status": "EXPECTED", "trace_id": "3" * 32}},
    }

    local_result = _apply_local_live_evidence_contract(
        result,
        mode="local-live-full",
        database_verification="PASS",
    )
    staging_result = _apply_local_live_evidence_contract(
        {"execution": "PASS"},
        mode="staging-live",
        database_verification="PASS",
    )

    assert local_result == {
        "execution": "PASS",
        "execution_mode": "LIVE",
        "provider_traces": {"guide_generation": {"status": "EXPECTED", "trace_id": "3" * 32}},
        "database_verification": "PASS",
        "provider_log_verification": "MANUAL_REQUIRED",
    }
    assert staging_result == {"execution": "PASS"}


@pytest.mark.parametrize(
    ("database_verification", "provider_traces", "expected_provider_verification"),
    [
        ("NOT_RUN", {}, "UNVERIFIED"),
        ("NOT_RUN", {"prescription_recognition": {"status": "EXPECTED", "trace_id": "2" * 32}}, "MANUAL_REQUIRED"),
        ("FAIL", {"guide_generation": {"status": "EXPECTED", "trace_id": None}}, "UNVERIFIED"),
        ("PASS", {"chat_generation": {"status": "EXPECTED", "trace_id": "4" * 32}}, "MANUAL_REQUIRED"),
    ],
)
def test_local_live_failure_reports_only_completed_verification_steps(
    database_verification: str,
    provider_traces: dict[str, dict[str, object]],
    expected_provider_verification: str,
) -> None:
    result = _apply_local_live_evidence_contract(
        {"execution": "FAIL", "provider_traces": provider_traces, "cleanup": "PENDING"},
        mode="local-live-full",
        database_verification=database_verification,
    )

    assert result["execution_mode"] == "LIVE"
    assert result["database_verification"] == database_verification
    assert result["provider_log_verification"] == expected_provider_verification
    assert result["cleanup"] == "PENDING"


def test_local_live_failure_preserves_provider_trace_references() -> None:
    traces = {"prescription_recognition": {"status": "EXPECTED", "trace_id": "2" * 32}}

    result = _failure(
        run_id="61a10000-0000-4000-8000-000000000003",
        mode="local-live-full",
        stage="OCR_REQUEST",
        provider_traces=traces,
    )

    assert result["provider_traces"] == traces


@pytest.mark.parametrize(
    ("enabled", "model_version", "prompt_version"),
    [
        (True, "gpt-4o-mini-2024-07-18", "ocr-structure-prompt-v2"),
        (False, None, None),
    ],
)
def test_ocr_database_evidence_matches_configured_structuring_path(
    enabled: bool,
    model_version: str | None,
    prompt_version: str | None,
) -> None:
    evidence = _ocr_database_evidence(
        SimpleNamespace(model_version=model_version, prompt_version=prompt_version),
        ocr_structuring_expected=enabled,
    )

    assert evidence == {
        "status": "PASS",
        "model_version": model_version,
        "prompt_version": prompt_version,
    }


@pytest.mark.parametrize(
    ("enabled", "model_version", "prompt_version"),
    [
        (True, None, None),
        (True, "model", None),
        (False, "unexpected-model", "unexpected-prompt"),
    ],
)
def test_ocr_database_evidence_rejects_config_and_database_mismatch(
    enabled: bool,
    model_version: str | None,
    prompt_version: str | None,
) -> None:
    with pytest.raises(HttpFlowError, match="DB_VERIFICATION") as exc_info:
        _ocr_database_evidence(
            SimpleNamespace(model_version=model_version, prompt_version=prompt_version),
            ocr_structuring_expected=enabled,
        )

    assert exc_info.value.evidence == {
        "api_code": "OCR_STRUCTURE_EVIDENCE_MISMATCH",
        "ocr_structuring_expected": enabled,
        "model_version_present": bool(model_version),
        "prompt_version_present": bool(prompt_version),
    }
