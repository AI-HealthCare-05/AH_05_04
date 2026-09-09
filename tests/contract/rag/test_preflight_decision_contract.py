"""Issue #173 Preflight decision contract test.

Three things are pinned here.

1. The kernel's state, decision and reason vocabulary matches the ``rag-runtime-v1.md`` fixed
   execution graph exactly, so a document edit or an enum edit cannot drift apart silently.
2. The kernel stays a pure slice: its module imports are stdlib only, with no DB, HTTP, lock,
   transaction, Retrieval or Provider dependency reachable at import time.
3. The synthetic decision matrix fixture reproduces the same decision, reason and manifest hash
   regardless of input order.

Passing this test proves determinism and scope, not approval or publication readiness.
``PUBLIC_TRACK_F_ENABLED`` stays false and RAG-12-API (#174), RAG-11 UI (#131) and RAG-12A (#175)
remain unimplemented.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag_runtime.identification_preflight import (  # noqa: E402
    MANIFEST_PROJECTION_VERSION,
    IdentificationSnapshotRef,
    MedicationIdentificationPreflightRequest,
    MedicationPreflightState,
    MedicationSnapshotRef,
    PreflightCurrentnessToken,
    PreflightDecision,
    PreflightExecutionStatus,
    PreflightReason,
    PreflightStaleProjection,
    PreflightStaleSignal,
    canonical_preflight_manifest_hash,
    evaluate_medication_identification_preflight,
    preflight_state_from_mapping,
    project_preflight_stale_signal,
    project_preflight_stale_signals,
)

KERNEL_PATH = PROJECT_ROOT / "rag_runtime" / "identification_preflight.py"
RUNTIME_CONTRACT_PATH = PROJECT_ROOT / "docs" / "contracts" / "targets" / "post-mvp-1" / "rag-runtime-v1.md"
SAFETY_CONTRACT_PATH = PROJECT_ROOT / "docs" / "contracts" / "targets" / "post-mvp-1" / "safety-result-v2.md"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "rag" / "preflight" / "decision_matrix.json"

# 이 kernel이 import해도 되는 stdlib module 전체 목록입니다. 여기에 DB·HTTP·Provider·Retrieval
# module을 추가하면 Issue #173의 "diff에 DB/endpoint/lock/transaction 구현이 0건" 기준이 깨집니다.
ALLOWED_KERNEL_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "re",
        "unicodedata",
    }
)


def read_graph_fallback_states() -> tuple[str, ...]:
    """Extract the identification-fallback branch vocabulary from the fixed execution graph."""
    text = RUNTIME_CONTRACT_PATH.read_text(encoding="utf-8")
    matches = re.findall(
        r"├─ ([A-Z_]+(?: \| [A-Z_]+)+)\n\s*│\s*→ approved_identification_fallback",
        text,
    )
    assert len(matches) == 1, f"고정 실행 Graph의 identification fallback 분기를 1건만 찾아야 합니다: {matches}"
    return tuple(token.strip() for token in matches[0].split("|"))


def test_medication_state_vocabulary_matches_fixed_execution_graph() -> None:
    graph_states = read_graph_fallback_states()

    assert graph_states == (
        "REVIEW_REQUIRED",
        "AMBIGUOUS",
        "UNAVAILABLE",
        "NOT_FOUND",
        "INVALID_INPUT",
        "UNRESOLVED",
    )
    # 선언 순서가 곧 대표 reason 우선순위이므로 순서까지 고정합니다.
    assert tuple(item.value for item in MedicationPreflightState) == ("MATCHED", *graph_states)


def test_reason_vocabulary_covers_every_graph_branch() -> None:
    graph_states = read_graph_fallback_states()
    text = RUNTIME_CONTRACT_PATH.read_text(encoding="utf-8")

    assert "├─ EXECUTION_CONTEXT_STALE" in text
    assert "└─ MATCHED" in text
    assert tuple(item.value for item in PreflightReason) == (
        "MATCHED",
        *graph_states,
        "EXECUTION_CONTEXT_STALE",
    )


def test_decision_axis_has_exactly_three_values() -> None:
    assert tuple(item.value for item in PreflightDecision) == (
        "PASS",
        "IDENTIFICATION_FALLBACK",
        "STALE_FALLBACK",
    )


def read_safety_contract_fallback_codes() -> tuple[str, ...]:
    """Extract public fallback_code vocabulary from safety-result-v2.md."""
    text = SAFETY_CONTRACT_PATH.read_text(encoding="utf-8")
    match = re.search(r"공개 `fallback_code`는 (.+?)로 제한한다\.", text)
    assert match is not None, "safety-result-v2.md에서 공개 fallback_code 문장을 찾을 수 없습니다."
    codes = re.findall(r"`([A-Z_]+)`", match.group(1))
    assert len(codes) >= 9, f"fallback_code 목록 파싱 실패: {codes}"
    return tuple(codes)


def read_safety_contract_internal_stale_reasons() -> tuple[str, ...]:
    """Extract internal stale_reason vocabulary from safety-result-v2.md."""
    text = SAFETY_CONTRACT_PATH.read_text(encoding="utf-8")
    match = re.search(r"- 내부 `stale_reason`: (.+)", text)
    assert match is not None, "safety-result-v2.md에서 내부 stale_reason 문장을 찾을 수 없습니다."
    reasons = re.findall(r"`([A-Z_]+)`", match.group(1))
    assert len(reasons) >= 5, f"stale_reason 목록 파싱 실패: {reasons}"
    return tuple(reasons)


def test_stale_signal_vocabulary_and_projection_matches_safety_result_contract() -> None:
    """Validate that PreflightStaleSignal and its downstream projection exact-match the safety contract.

    Addresses review [P2]:
    1. Exact-matches the public fallback_code list and internal stale_reason list parsed from
       safety-result-v2.md without relying on loose substring checks.
    2. Distinguishes the two roles: PRESCRIPTION_STALE is a public fallback_code (no internal
       stale_reason), while IDENTIFICATION_STALE and RUNTIME_RELEASE_STALE are internal stale_reasons
       projected onto the public EXECUTION_CONTEXT_STALE fallback_code.
    3. Pins the downstream projection consumed by RAG-12-API (#174).
    """
    fallback_codes = read_safety_contract_fallback_codes()
    stale_reasons = read_safety_contract_internal_stale_reasons()

    # 1. 정본의 공개 fallback_code 및 내부 stale_reason exact-match 고정
    assert fallback_codes == (
        "NO_APPROVED_EVIDENCE",
        "CONFLICTING_EVIDENCE",
        "SAFETY_ROUTED",
        "PROVIDER_TIMEOUT",
        "DEPENDENCY_UNAVAILABLE",
        "VALIDATION_FAILED",
        "PRESCRIPTION_STALE",
        "EXECUTION_CONTEXT_STALE",
        "UNSUPPORTED_REQUEST",
    )
    assert stale_reasons == (
        "PATIENT_CONTEXT_STALE",
        "IDENTIFICATION_STALE",
        "RUNTIME_RELEASE_STALE",
        "RUNTIME_ENVIRONMENT_SUSPENDED",
        "RESOLVER_MEMBER_REVOKED",
    )

    # 2. Kernel enum 축 어휘 고정
    assert tuple(item.value for item in PreflightStaleSignal) == (
        "PRESCRIPTION_STALE",
        "IDENTIFICATION_STALE",
        "RUNTIME_RELEASE_STALE",
    )

    # 3. PreflightStaleSignal -> (fallback_code, stale_reason) 사영 검증
    # - 처방 버전 불일치: 공개 PRESCRIPTION_STALE, 내부 stale_reason 없음 (None)
    proj_rx = project_preflight_stale_signal(PreflightStaleSignal.PRESCRIPTION_STALE)
    assert isinstance(proj_rx, PreflightStaleProjection)
    assert proj_rx.fallback_code == "PRESCRIPTION_STALE"
    assert proj_rx.fallback_code in fallback_codes
    assert proj_rx.stale_reason is None

    # - 식별 불일치: 공개 EXECUTION_CONTEXT_STALE, 내부 stale_reason IDENTIFICATION_STALE
    proj_id = project_preflight_stale_signal(PreflightStaleSignal.IDENTIFICATION_STALE)
    assert isinstance(proj_id, PreflightStaleProjection)
    assert proj_id.fallback_code == "EXECUTION_CONTEXT_STALE"
    assert proj_id.fallback_code in fallback_codes
    assert proj_id.stale_reason == "IDENTIFICATION_STALE"
    assert proj_id.stale_reason in stale_reasons

    # - 런타임 번들 불일치: 공개 EXECUTION_CONTEXT_STALE, 내부 stale_reason RUNTIME_RELEASE_STALE
    proj_bundle = project_preflight_stale_signal(PreflightStaleSignal.RUNTIME_RELEASE_STALE)
    assert isinstance(proj_bundle, PreflightStaleProjection)
    assert proj_bundle.fallback_code == "EXECUTION_CONTEXT_STALE"
    assert proj_bundle.fallback_code in fallback_codes
    assert proj_bundle.stale_reason == "RUNTIME_RELEASE_STALE"
    assert proj_bundle.stale_reason in stale_reasons

    # 4. 복합 STALE 신호 집계(aggregation) 및 우선순위 불변식 검증
    # - 처방 버전 변경(PRESCRIPTION_STALE)이 최우선: 어떤 신호와 결합해도 단일 결과는 PRESCRIPTION_STALE
    proj_compound_rx_bundle = project_preflight_stale_signals(
        (PreflightStaleSignal.PRESCRIPTION_STALE, PreflightStaleSignal.RUNTIME_RELEASE_STALE)
    )
    assert proj_compound_rx_bundle == proj_rx

    proj_compound_all = project_preflight_stale_signals(
        (
            PreflightStaleSignal.RUNTIME_RELEASE_STALE,
            PreflightStaleSignal.IDENTIFICATION_STALE,
            PreflightStaleSignal.PRESCRIPTION_STALE,
        )
    )
    assert proj_compound_all == proj_rx

    # - 처방 버전 불일치가 없을 때는 식별 불일치(IDENTIFICATION_STALE)가 런타임 번들(RUNTIME_RELEASE_STALE)에 우선
    proj_compound_id_bundle = project_preflight_stale_signals(
        (PreflightStaleSignal.RUNTIME_RELEASE_STALE, PreflightStaleSignal.IDENTIFICATION_STALE)
    )
    assert proj_compound_id_bundle == proj_id

    # - 빈 신호는 None
    assert project_preflight_stale_signals(()) is None


def test_kernel_imports_are_stdlib_only() -> None:
    tree = ast.parse(KERNEL_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "상대 import는 순수 경계를 흐립니다."
            imported.add(node.module or "")

    assert imported <= ALLOWED_KERNEL_IMPORTS, f"허용되지 않은 import: {sorted(imported - ALLOWED_KERNEL_IMPORTS)}"


def test_rag_runtime_package_exports_all_public_symbols() -> None:
    import inspect

    import rag_runtime
    import rag_runtime.identification_preflight as kernel

    assert hasattr(rag_runtime, "__all__")
    assert isinstance(rag_runtime.__all__, list)
    kernel_defined = {
        name
        for name, obj in inspect.getmembers(kernel)
        if not name.startswith("_") and getattr(obj, "__module__", None) == kernel.__name__
    }
    kernel_defined.add("MANIFEST_PROJECTION_VERSION")
    assert set(rag_runtime.__all__) == kernel_defined
    for symbol in rag_runtime.__all__:
        assert getattr(rag_runtime, symbol) is getattr(kernel, symbol)


def test_manifest_projection_version_is_pinned() -> None:
    assert MANIFEST_PROJECTION_VERSION == "medication-identification-preflight-manifest-v1"


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def build_request(fixture: dict, case: dict) -> MedicationIdentificationPreflightRequest:
    version_id = fixture["prescription_version_id"]
    bundle_id = fixture["runtime_release_bundle_id"]
    token = PreflightCurrentnessToken(
        prescription_id=fixture["prescription_id"],
        pinned_prescription_version_id=version_id,
        observed_active_prescription_version_id=case.get("observed_active_prescription_version_id", version_id),
        pinned_runtime_release_bundle_id=bundle_id,
        observed_active_runtime_release_bundle_id=case.get("observed_active_runtime_release_bundle_id", bundle_id),
        ownership_verified=case.get("ownership_verified", True),
    )
    medications = tuple(
        MedicationSnapshotRef(
            prescription_version_medication_id=entry["prescription_version_medication_id"],
            prescription_version_id=version_id,
            display_order=entry["display_order"],
        )
        for entry in case["medications"]
    )
    identifications = tuple(
        IdentificationSnapshotRef(
            prescription_version_medication_id=entry["prescription_version_medication_id"],
            state=preflight_state_from_mapping(entry),  # type: ignore[arg-type]
            prescription_version_id=version_id,
            identification_id=entry.get("identification_id"),
            code_system=entry.get("code_system"),
            canonical_code=entry.get("canonical_code"),
            runtime_release_bundle_id=bundle_id if entry.get("state") == "MATCHED" else None,
        )
        for entry in case["medications"]
    )
    return MedicationIdentificationPreflightRequest(token, medications, identifications)


def fixture_cases() -> list[tuple[str, dict, dict]]:
    fixture = load_fixture()
    return [(case["case_id"], fixture, case) for case in fixture["cases"]]


def test_fixture_carries_no_real_identity_or_insurance_code() -> None:
    raw = FIXTURE_PATH.read_text(encoding="utf-8")

    assert "insurance_code" not in raw
    assert "medication_name" not in raw
    assert "strength_text" not in raw
    for entry in (case for _, _, case in fixture_cases()):
        for medication in entry["medications"]:
            code = medication.get("canonical_code")
            assert code is None or code.startswith("SYNTHETIC-")


@pytest.mark.parametrize(
    ("case_id", "fixture", "case"), fixture_cases(), ids=lambda value: value if isinstance(value, str) else ""
)
def test_decision_matrix_case(case_id: str, fixture: dict, case: dict) -> None:
    expected = case["expected"]
    outcome = evaluate_medication_identification_preflight(build_request(fixture, case))

    assert outcome.execution_status is PreflightExecutionStatus(expected["execution_status"]), case_id
    assert outcome.decision is PreflightDecision(expected["decision"]), case_id
    assert outcome.reason is PreflightReason(expected["reason"]), case_id
    assert [item.value for item in outcome.identification_reasons] == expected["identification_reasons"], case_id
    assert [item.value for item in outcome.stale_signals] == expected["stale_signals"], case_id
    if outcome.stale_signals:
        assert outcome.primary_stale_projection == project_preflight_stale_signals(outcome.stale_signals), case_id
    else:
        assert outcome.primary_stale_projection is None, case_id
    assert (outcome.manifest_hash is not None) is expected["manifest_hash_present"], case_id
    if "blocking_medication_ids" in expected:
        assert list(outcome.blocking_medication_ids) == expected["blocking_medication_ids"], case_id
    if "validation_codes" in expected:
        assert [item.value for item in outcome.validation_codes] == expected["validation_codes"], case_id


@pytest.mark.parametrize(
    ("case_id", "fixture", "case"), fixture_cases(), ids=lambda value: value if isinstance(value, str) else ""
)
def test_decision_matrix_case_is_order_independent(case_id: str, fixture: dict, case: dict) -> None:
    forward = build_request(fixture, case)
    reversed_request = MedicationIdentificationPreflightRequest(
        currentness=forward.currentness,
        medications=tuple(reversed(forward.medications)),
        identifications=tuple(reversed(forward.identifications)),
    )

    assert evaluate_medication_identification_preflight(forward) == evaluate_medication_identification_preflight(
        reversed_request
    ), case_id
    if case["expected"]["manifest_hash_present"]:
        assert canonical_preflight_manifest_hash(forward) == canonical_preflight_manifest_hash(reversed_request), (
            case_id
        )
