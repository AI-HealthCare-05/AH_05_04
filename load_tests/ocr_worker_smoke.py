"""#627 OCR / Worker minimum smoke scenario.

This scenario uses only the approved synthetic one-cycle OCR fixture. It is
intended for baseline/smoke load evidence, not production capacity approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from functools import cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from locust import HttpUser, between, task  # type: ignore[import-not-found]

DEFAULT_MANIFEST_PATH = "backend/app/release_validation/scenarios/ai-one-cycle-clova-openai-v1.json"
DEFAULT_FIXTURE_PATH = "tests/fixtures/release_validation/ai_one_cycle_clova_openai_v1.png"
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_WAIT_SECONDS = 60.0
DEFAULT_IDEMPOTENCY_PREFIX = "load-test-ocr"
FLOW_FAILURE_REQUEST_TYPE = "FLOW"
FLOW_FAILURE_NAME = "ocr-smoke:flow-failed"
TERMINAL_FAILURE_STATUSES = {"FAILED", "CANCELLED", "STALE"}

_FIELD_VALUE_KEYS = {
    (0, "PRESCRIBED_DATE"): ("prescribed_date",),
    (1, "MEDICATION_NAME"): ("medications", 0, "medication_name"),
    (1, "MEDICATION_STRENGTH"): ("medications", 0, "strength_text"),
    (1, "DOSE_VALUE"): ("medications", 0, "dose_value"),
    (1, "DOSE_UNIT"): ("medications", 0, "dose_unit"),
    (1, "FREQUENCY_PER_DAY"): ("medications", 0, "frequency_per_day"),
    (1, "DURATION_DAYS"): ("medications", 0, "duration_days"),
    (1, "TIMING"): ("medications", 0, "timing_text"),
}


class OcrWorkerSmokeUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task
    def upload_ocr_review_confirm_flow(self) -> None:
        manifest = _manifest()
        fixture_path = _fixture_path(manifest)
        _validate_fixture_hash(fixture_path, manifest)
        headers = _auth_headers()

        document_id = self._upload_document_id(fixture_path, headers)
        if document_id is None:
            return

        completed_job = self._create_and_wait_for_ocr_job(document_id, headers)
        if completed_job is None:
            return

        if not self._review_ocr_result(completed_job, manifest, headers):
            return

        prescription_id = self._confirm_prescription_id(document_id, headers)
        if prescription_id is None:
            return

        self._json_request(
            "get",
            f"/api/v1/prescriptions/{prescription_id}",
            200,
            "ocr-smoke:get-prescription",
            headers=headers,
        )

    def _upload_document_id(self, fixture_path: Path, headers: dict[str, str]) -> str | None:
        upload = self._upload_document(fixture_path, headers)
        if upload is None:
            return None
        document_id = upload.get("document_id")
        if not document_id:
            self._record_flow_failure("missing-document-id")
            return None
        return str(document_id)

    def _create_and_wait_for_ocr_job(self, document_id: str, headers: dict[str, str]) -> dict[str, Any] | None:
        accepted = self._accept_ocr_job(document_id, headers)
        if accepted is None:
            return None
        completed = self._wait_for_completed_job(accepted, headers)
        if completed is None:
            return None
        return {**accepted, **completed}

    def _review_ocr_result(
        self,
        completed_job: dict[str, Any],
        manifest: dict[str, Any],
        headers: dict[str, str],
    ) -> bool:
        result_url = _ocr_result_url(completed_job)
        ocr_result = self._json_request("get", result_url, 200, "ocr-smoke:get-ocr-result", headers=headers)
        if ocr_result is None:
            return False
        return self._review_extracted_fields(ocr_result, manifest, headers)

    def _confirm_prescription_id(self, document_id: str, headers: dict[str, str]) -> str | None:
        prescription = self._json_request(
            "post",
            f"/api/v1/documents/{document_id}/prescription",
            201,
            "ocr-smoke:confirm-prescription",
            headers=headers,
        )
        if prescription is None:
            return None
        prescription_id = prescription.get("prescription_id")
        if not prescription_id:
            self._record_flow_failure("missing-prescription-id")
            return None
        return str(prescription_id)

    def _upload_document(self, fixture_path: Path, headers: dict[str, str]) -> dict[str, Any] | None:
        files = {"file": (fixture_path.name, fixture_path.read_bytes(), "image/png")}
        data = {"document_type": "PRESCRIPTION"}
        return self._json_request(
            "post",
            "/api/v1/documents",
            201,
            "ocr-smoke:upload-document",
            headers=headers,
            files=files,
            data=data,
        )

    def _accept_ocr_job(self, document_id: str, headers: dict[str, str]) -> dict[str, Any] | None:
        request_headers = {
            **headers,
            "Idempotency-Key": f"{_idempotency_prefix()}-{uuid4()}",
        }
        return self._json_request(
            "post",
            f"/api/v1/documents/{document_id}/ocr-jobs",
            202,
            "ocr-smoke:create-ocr-job",
            headers=request_headers,
            json={"force_reprocess": False},
        )

    def _wait_for_completed_job(self, accepted: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
        status_url = accepted.get("status_url")
        if not status_url:
            job_id = accepted.get("job_id")
            if not job_id:
                raise RuntimeError("OCR accepted response did not include status_url or job_id")
            status_url = f"/api/v1/jobs/{job_id}"

        deadline = time.monotonic() + _max_wait_seconds()
        while time.monotonic() < deadline:
            data = self._json_request("get", status_url, 200, "ocr-smoke:poll-job", headers=headers)
            if data is None:
                return None
            status = data.get("status")
            if status == "COMPLETED":
                return data
            if status in TERMINAL_FAILURE_STATUSES:
                self._record_flow_failure("terminal-status")
                return None
            time.sleep(_retry_after_seconds(data))

        self._record_flow_failure("timeout")
        return None

    def _review_extracted_fields(
        self,
        ocr_result: dict[str, Any],
        manifest: dict[str, Any],
        headers: dict[str, str],
    ) -> bool:
        expected_values = _expected_confirmed_values(manifest)
        reviewed_identities: set[tuple[int, str]] = set()
        for field in ocr_result.get("fields", []):
            identity = (int(field.get("medication_index", -1)), str(field.get("field_type", "")))
            if identity not in expected_values:
                continue
            reviewed_identities.add(identity)
            reviewed = self._json_request(
                "patch",
                f"/api/v1/extracted-fields/{field['field_id']}",
                200,
                "ocr-smoke:review-field",
                headers=headers,
                json={"confirmed_value": expected_values[identity]},
            )
            if reviewed is None:
                return False
        missing = set(expected_values) - reviewed_identities
        if missing:
            self._record_flow_failure("missing-required-fields")
            return False
        return True

    def _json_request(
        self,
        method: str,
        path: str,
        expected_status: int,
        name: str,
        *,
        headers: dict[str, str],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        request = getattr(self.client, method)
        with request(path, headers=headers, name=name, catch_response=True, **kwargs) as response:
            if response.status_code != expected_status:
                response.failure(f"expected {expected_status}, got {response.status_code}")
                return None
            try:
                body = response.json()
            except ValueError:
                response.failure("expected JSON response")
                return None
            data = body.get("data")
            if not isinstance(data, dict):
                response.failure("expected JSON object in response data")
                return None
            return data

    def _record_flow_failure(self, reason: str) -> None:
        _record_flow_failure(self.environment, reason)


@cache
def _manifest() -> dict[str, Any]:
    return _load_json(_configured_path("LOAD_TEST_OCR_MANIFEST_PATH", DEFAULT_MANIFEST_PATH))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_path(manifest: dict[str, Any]) -> Path:
    return _configured_path("LOAD_TEST_OCR_FIXTURE_PATH", str(manifest.get("fixture_path") or DEFAULT_FIXTURE_PATH))


def _configured_path(env_name: str, default: str) -> Path:
    value = os.environ.get(env_name, default).strip()
    path = Path(value)
    if not path.is_absolute():
        path = _repo_root() / path
    return path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _validate_fixture_hash(fixture_path: Path, manifest: dict[str, Any]) -> None:
    expected = str(manifest.get("fixture_sha256") or "").removeprefix("sha256:")
    if not expected:
        raise RuntimeError("OCR load-test manifest must include fixture_sha256")
    actual = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError("OCR load-test fixture hash does not match the scenario manifest")


def _ocr_result_url(job_data: dict[str, Any]) -> str:
    result_url = job_data.get("result_url")
    if result_url:
        return str(result_url)
    domain_id = job_data.get("domain_id")
    if not domain_id:
        raise RuntimeError("OCR job response did not include result_url or domain_id")
    return f"/api/v1/ocr-jobs/{domain_id}"


def _expected_confirmed_values(manifest: dict[str, Any]) -> dict[tuple[int, str], str]:
    values: dict[tuple[int, str], str] = {}
    for medication_index, field_type in manifest.get("expected_field_identities", []):
        identity = (int(medication_index), str(field_type))
        value = _lookup_manifest_value(manifest, _FIELD_VALUE_KEYS[identity])
        values[identity] = str(value)
    return values


def _lookup_manifest_value(manifest: dict[str, Any], key_path: tuple[str | int, ...]) -> Any:
    current: Any = manifest
    for key in key_path:
        current = current[key]
    return current


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("LOAD_TEST_BEARER_TOKEN", "").strip()
    if not token:
        raise RuntimeError("LOAD_TEST_BEARER_TOKEN is required for OCR / Worker smoke")
    return {"Accept": "application/json", "Authorization": f"Bearer {token}"}


def _record_flow_failure(environment: Any, reason: str) -> None:
    environment.events.request.fire(
        request_type=FLOW_FAILURE_REQUEST_TYPE,
        name=FLOW_FAILURE_NAME,
        response_time=0,
        response_length=0,
        exception=RuntimeError(f"OCR smoke flow failed: {reason}"),
        context={"reason": reason},
    )


def _idempotency_prefix() -> str:
    return (
        os.environ.get("LOAD_TEST_OCR_IDEMPOTENCY_PREFIX", DEFAULT_IDEMPOTENCY_PREFIX).strip()
        or DEFAULT_IDEMPOTENCY_PREFIX
    )


def _max_wait_seconds() -> float:
    return _positive_float("LOAD_TEST_OCR_MAX_WAIT_SECONDS", DEFAULT_MAX_WAIT_SECONDS)


def _retry_after_seconds(job_data: dict[str, Any]) -> float:
    retry_after = job_data.get("retry_after_seconds")
    if retry_after is None:
        return _positive_float("LOAD_TEST_OCR_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    try:
        return max(float(retry_after), 0.1)
    except (TypeError, ValueError):
        return _positive_float("LOAD_TEST_OCR_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)


def _positive_float(env_name: str, default: float) -> float:
    raw_value = os.environ.get(env_name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{env_name} must be a positive number") from exc
    if value <= 0:
        raise RuntimeError(f"{env_name} must be a positive number")
    return value
