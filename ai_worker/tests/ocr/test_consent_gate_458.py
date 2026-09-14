"""Shared #465 decisions and per-call #458 checks; synthetic providers only."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ai_worker.tasks.ocr.consent import (
    ConsentCheckedLlmStructurer,
    ConsentRow,
    ConsentSnapshot,
    OcrConsentDeniedError,
    OcrConsentGate,
    consent_denial,
)
from ocr_runtime.structuring import OcrStructureResult

_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/fixtures/consent/consent_gate_207_cases.json").read_text()
)


def allowed():
    return ConsentSnapshot(ConsentRow("OCR", "GRANTED", "synthetic-policy"), "ACTIVE", True)


def gate(repository):
    return OcrConsentGate(repository, uuid4(), uuid4(), lambda: "synthetic-policy")


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=lambda case: case["case_id"])
async def test_shared_207_fixture(case):
    snapshot = ConsentSnapshot(
        ConsentRow(**case["row"]) if case["row"] else None, case["account_status"], case["resource_owner_matches"]
    )
    reason = (
        "LOOKUP_FAILED"
        if case["lookup_error"]
        else consent_denial(
            snapshot,
            purpose=case["purpose"],
            policy_version=_FIXTURE["current_policy_versions"][case["purpose"]],
        )
    )
    assert reason == case["expected"]["reason"]
    assert (reason is None) == case["expected"]["allowed"]
    # Exercise the real OCR gate for every generic case, preserving each mismatch.
    repository = AsyncMock()
    if case["lookup_error"]:
        repository.get_snapshot.side_effect = RuntimeError("SYNTHETIC_PRIVATE_458")
    else:
        row = snapshot.row
        if row and row.purpose == case["purpose"]:
            row = ConsentRow("OCR", row.status, row.policy_version)
        repository.get_snapshot.return_value = ConsentSnapshot(
            row, snapshot.account_status, snapshot.resource_owner_matches
        )
    checker = OcrConsentGate(repository, uuid4(), uuid4(), lambda: _FIXTURE["current_policy_versions"][case["purpose"]])
    provider = AsyncMock(return_value="done")
    if case["expected"]["allowed"]:
        assert await checker.run(provider) == "done"
        provider.assert_awaited_once()
    else:
        with pytest.raises(OcrConsentDeniedError) as caught:
            await checker.run(provider)
        assert caught.value.reason == case["expected"]["reason"]
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None
        provider.assert_not_awaited()


@pytest.mark.parametrize(
    "row", [ConsentRow("OCR", "UNKNOWN", "synthetic-policy"), ConsentRow("OCR", "GRANTED", "synthetic-policy", False)]
)
async def test_malformed_consent_does_not_allow_provider(row):
    repository = AsyncMock()
    repository.get_snapshot.return_value = ConsentSnapshot(row, "ACTIVE", True)
    provider = AsyncMock()
    with pytest.raises(OcrConsentDeniedError, match="INVALID_CONSENT"):
        await gate(repository).run(provider)
    provider.assert_not_awaited()


async def test_clova_then_withdrawal_prevents_llm_and_does_not_return_result():
    repository = AsyncMock()
    repository.get_snapshot.side_effect = [
        allowed(),
        ConsentSnapshot(ConsentRow("OCR", "WITHDRAWN", "synthetic-policy"), "ACTIVE", True),
    ]
    checker = gate(repository)
    clova = AsyncMock(return_value=[])
    llm = AsyncMock()
    structurer = ConsentCheckedLlmStructurer(checker, llm)
    raw = await checker.run(clova)
    with pytest.raises(OcrConsentDeniedError, match="WITHDRAWN"):
        await structurer.structure(raw)
    clova.assert_awaited_once()
    llm.structure.assert_not_awaited()
    assert repository.get_snapshot.await_count == 2
    assert repository.get_snapshot.await_args_list[0] == repository.get_snapshot.await_args_list[1]


async def test_valid_two_checks_preserve_structure_result():
    repository = AsyncMock()
    repository.get_snapshot.return_value = allowed()
    checker = gate(repository)
    llm = AsyncMock()
    expected = OcrStructureResult([], "synthetic-model", "synthetic-prompt")
    llm.structure.return_value = expected
    raw = await checker.run(AsyncMock(return_value=[]))
    assert await ConsentCheckedLlmStructurer(checker, llm).structure(raw) is expected
    assert repository.get_snapshot.await_count == 2


async def test_lookup_failure_after_clova_blocks_llm_without_exception_chain():
    repository = AsyncMock()
    repository.get_snapshot.side_effect = [allowed(), RuntimeError("SYNTHETIC_PRIVATE_458")]
    checker = gate(repository)
    await checker.run(AsyncMock(return_value=[]))
    llm = AsyncMock()
    with pytest.raises(OcrConsentDeniedError) as caught:
        await ConsentCheckedLlmStructurer(checker, llm).structure([])
    assert str(caught.value) == "LOOKUP_FAILED"
    assert caught.value.__context__ is None
    llm.structure.assert_not_awaited()


async def test_task_cancellation_is_not_consent_denial():
    repository = AsyncMock()
    repository.get_snapshot.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await gate(repository).check()


async def test_empty_current_policy_blocks_external_call():
    repository = AsyncMock()
    repository.get_snapshot.return_value = allowed()
    checker = OcrConsentGate(repository, uuid4(), uuid4(), lambda: " ")
    provider = AsyncMock()
    with pytest.raises(OcrConsentDeniedError, match="POLICY_VERSION_MISMATCH"):
        await checker.run(provider)
    provider.assert_not_awaited()


async def test_actual_clova_engine_boundary_stops_llm_after_withdrawal(tmp_path, monkeypatch):
    import time

    from ocr_runtime.clova_engine import ClovaOcrEngine
    from provider_contracts.ocr import OcrDeadline, OcrRecognitionResult

    repository = AsyncMock()
    repository.get_snapshot.side_effect = [
        allowed(),
        ConsentSnapshot(ConsentRow("OCR", "WITHDRAWN", "synthetic-policy"), "ACTIVE", True),
    ]
    checker = gate(repository)
    llm = AsyncMock()
    engine = ClovaOcrEngine(
        invoke_url="https://synthetic.invalid",
        secret_key="synthetic",
        storage_dir=str(tmp_path),
        timeout_seconds=5,
        structurer=ConsentCheckedLlmStructurer(checker, llm),
        observability_disabled=True,
    )
    (tmp_path / "synthetic.png").write_bytes(b"synthetic")
    clova = AsyncMock(return_value=OcrRecognitionResult(raw_fields=[]))
    monkeypatch.setattr(engine, "_recognize_provider", clova)
    with pytest.raises(OcrConsentDeniedError, match="WITHDRAWN"):
        await checker.run(
            lambda: engine.recognize(
                object_key="synthetic.png",
                file_mime_type="image/png",
                deadline=OcrDeadline(provider_path_deadline=time.monotonic() + 5),
            )
        )
    clova.assert_awaited_once()
    llm.structure.assert_not_awaited()


async def test_policy_change_between_external_calls_blocks_old_consent():
    repository = AsyncMock()
    repository.get_snapshot.return_value = allowed()
    current = {"version": "synthetic-policy"}
    checker = OcrConsentGate(repository, uuid4(), uuid4(), lambda: current["version"])
    await checker.run(AsyncMock(return_value=[]))
    current["version"] = "synthetic-policy-next"
    llm = AsyncMock()
    with pytest.raises(OcrConsentDeniedError, match="POLICY_VERSION_MISMATCH"):
        await ConsentCheckedLlmStructurer(checker, llm).structure([])
    llm.structure.assert_not_awaited()
