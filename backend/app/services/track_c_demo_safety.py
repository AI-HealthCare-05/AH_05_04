"""Version-pinned symptom routing for a time-limited Local synthetic demo only."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.core.config import Config, Env
from app.core.errors import ApiError
from app.models.track_c import SafetyDisposition, SafetyResponseLevel
from app.services.track_c_flow import ContractFoundationSafetyPolicy, SafetyPolicyResult

DEMO_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[1] / "config/track_c/safety-demo/track-c-safety-demo-2026-09-16.1.json"
)
DEMO_ARTIFACT_SHA256 = "ba2579c1299ba800fca2d23c450a7d9abaf2f47bcec5490eb8c61ec0c9a7893b"


def demo_unavailable() -> ApiError:
    return ApiError(
        status_code=503,
        code="SAFETY_DEMO_UNAVAILABLE",
        message="내부 시연용 안전 확인을 사용할 수 없습니다. 담당자에게 확인해 주세요.",
    )


class InternalDemoSafetyPolicy:
    """No Provider, inferred symptoms, diagnosis or treatment recommendations."""

    def __init__(self, settings: Config) -> None:
        self._settings = settings

    def evaluate(self, symptom_codes: tuple[str, ...], *, user_id: UUID | None = None) -> SafetyPolicyResult:
        settings = self._settings
        start = settings.TRACK_C_SAFETY_DEMO_STARTS_AT
        end = settings.TRACK_C_SAFETY_DEMO_EXPIRES_AT
        now = datetime.now(UTC)
        # Recheck on each new mutation, including after the server has been up for seven days.
        if (
            not settings.TRACK_C_SAFETY_DEMO_ENABLED
            or settings.ENV is not Env.LOCAL
            or user_id not in settings.TRACK_C_SAFETY_DEMO_USER_IDS
            or start is None
            or end is None
            or start.utcoffset() is None
            or end.utcoffset() is None
            or not timedelta(0) < end - start <= timedelta(days=7)
            or not start <= now < end
        ):
            raise demo_unavailable()
        try:
            raw = DEMO_ARTIFACT_PATH.read_bytes()
        except OSError:
            raise demo_unavailable() from None
        # Rule, copy and Source are an indivisible versioned artifact, not clinical approval.
        if hashlib.sha256(raw).hexdigest() != DEMO_ARTIFACT_SHA256:
            raise demo_unavailable()
        artifact = json.loads(raw)
        # Preserve the existing [] meaning and versions; do not imply recent-24-hour confirmation.
        if not symptom_codes:
            return ContractFoundationSafetyPolicy().evaluate(symptom_codes)
        selected = set(symptom_codes)
        levels = {choice["response_level"] for choice in artifact["choices"] if choice["code"] in selected}
        if "EMERGENCY" in levels:
            level, disposition = SafetyResponseLevel.EMERGENCY, SafetyDisposition.EMERGENCY_ROUTED
        elif "URGENT" in levels:
            level, disposition = SafetyResponseLevel.URGENT, SafetyDisposition.URGENT_ROUTED
        else:
            level, disposition = SafetyResponseLevel.UNKNOWN, SafetyDisposition.UNKNOWN_RISK
        return SafetyPolicyResult(
            response_level=level,
            safety_disposition=disposition,
            message_code=artifact["messages"][level.value]["message_code"],
            copy_version=artifact["copy_version"],
            source_version=artifact["source_version"],
        )
