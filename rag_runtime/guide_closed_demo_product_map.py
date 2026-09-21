"""Exact, fail-closed sealed 17-product mapping for Guide CLOSED_DEMO.

This module is an immutable artifact reader that deterministically resolves
prescribed medication names and strengths to official MFDS_ITEM_SEQ codes.
It forbids fuzzy mapping, guessing, or substring matches.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_runtime.query_binding import ImmutableArtifactRef

GUIDE_CLOSED_DEMO_PRODUCT_MAP_RESOURCE = (
    Path(__file__).resolve().parent / "resources" / "guide-closed-demo-17p-product-map-v1.json"
)

_APPROVED_PRODUCT_MAP_SHA256 = "5f1e26adb671faac2f8143a34eb3a743d1b2ad8fc7bcaf63da9146eeea9acb3e"
_PROJECTION_VERSION = "guide-closed-demo-17p-product-map@1"
_ENVIRONMENT = "CLOSED_DEMO"
_PRODUCT_COUNT = 17


class GuideClosedDemoProductMapError(RuntimeError):
    """The sealed 17-product mapping artifact is invalid or unreadable."""


@dataclass(frozen=True, slots=True)
class GuideClosedDemoProductMap:
    """Read-only container for the sealed 17-product mappings."""

    artifact_ref: ImmutableArtifactRef
    product_count: int
    exact_mapping: dict[tuple[str, str | None], str]

    def resolve(
        self,
        medication_name: str,
        strength: str | None = None,
    ) -> str | None:
        """Resolve a normalized (name, strength) pair to an official MFDS_ITEM_SEQ.

        Returns the 9-digit item_seq if matched exactly, or None if not present in the sealed map.
        """
        normalized_name = _normalize_text(medication_name)
        normalized_strength = _normalize_text(strength) if strength is not None else None

        # Try (name, strength) exact match
        key = (normalized_name, normalized_strength)
        if key in self.exact_mapping:
            return self.exact_mapping[key]

        # If strength was provided, also try (name, None) fallback only if unambiguous in map
        if normalized_strength is not None:
            name_only_key = (normalized_name, None)
            if name_only_key in self.exact_mapping:
                return self.exact_mapping[name_only_key]

        return None


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFC", value).strip()
    return re.sub(r"\s+", " ", normalized)


def _canonical_json_sha256(
    value: dict[str, Any],
    *,
    excluded_top_level_keys: frozenset[str] = frozenset(),
) -> str:
    if excluded_top_level_keys and isinstance(value, dict):
        value = {key: item for key, item in value.items() if key not in excluded_top_level_keys}
    canonical_bytes = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def _verify_manifest_envelope(manifest: dict[str, Any]) -> ImmutableArtifactRef:
    if manifest.get("projection_version") != _PROJECTION_VERSION:
        raise GuideClosedDemoProductMapError("Invalid projection_version in product map")

    if manifest.get("environment") != _ENVIRONMENT:
        raise GuideClosedDemoProductMapError("Invalid environment in product map")

    if manifest.get("product_count") != _PRODUCT_COUNT:
        raise GuideClosedDemoProductMapError(f"Product map must contain exactly {_PRODUCT_COUNT} products")

    artifact_ref_raw = manifest.get("artifact_ref")
    if not isinstance(artifact_ref_raw, dict):
        raise GuideClosedDemoProductMapError("artifact_ref must be an object")

    content_sha256 = artifact_ref_raw.get("content_sha256")
    if content_sha256 != _APPROVED_PRODUCT_MAP_SHA256:
        raise GuideClosedDemoProductMapError("Approved product map SHA256 does not match sealed constant")

    calculated_sha256 = _canonical_json_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"artifact_ref"}),
    )
    if calculated_sha256 != _APPROVED_PRODUCT_MAP_SHA256:
        raise GuideClosedDemoProductMapError("Computed canonical SHA256 does not match approved hash")

    return ImmutableArtifactRef(
        artifact_code=str(artifact_ref_raw.get("artifact_code")),
        version=str(artifact_ref_raw.get("version")),
        content_sha256=content_sha256,
    )


def _extract_exact_mapping(products: list[Any]) -> dict[tuple[str, str | None], str]:
    exact_mapping: dict[tuple[str, str | None], str] = {}
    for prod in products:
        if not isinstance(prod, dict):
            raise GuideClosedDemoProductMapError("Invalid product entry")
        item_seq = str(prod.get("item_seq", "")).strip()
        if not item_seq.isdigit() or len(item_seq) != 9:
            raise GuideClosedDemoProductMapError(f"Invalid MFDS item_seq: {item_seq}")

        matches = prod.get("exact_matches")
        if not isinstance(matches, list) or not matches:
            raise GuideClosedDemoProductMapError(f"Product {item_seq} has no exact matches")

        for m in matches:
            if not isinstance(m, dict):
                raise GuideClosedDemoProductMapError("Invalid match entry")
            m_name = _normalize_text(m.get("medication_name"))
            raw_strength = m.get("strength")
            m_strength = _normalize_text(raw_strength) if raw_strength is not None else None
            key = (m_name, m_strength)
            if key in exact_mapping and exact_mapping[key] != item_seq:
                raise GuideClosedDemoProductMapError(
                    f"Ambiguous mapping key {key} between {exact_mapping[key]} and {item_seq}"
                )
            exact_mapping[key] = item_seq
    return exact_mapping


def load_guide_closed_demo_product_map(
    path: Path | str | None = None,
) -> GuideClosedDemoProductMap:
    """Load the sealed 17-product mapping artifact with strict SHA256 verification."""
    target_path = Path(path) if path is not None else GUIDE_CLOSED_DEMO_PRODUCT_MAP_RESOURCE
    try:
        raw_text = target_path.read_text(encoding="utf-8")
        manifest = json.loads(raw_text)
    except OSError as exc:
        raise GuideClosedDemoProductMapError(f"Cannot read product map file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GuideClosedDemoProductMapError(f"Product map is not valid JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise GuideClosedDemoProductMapError("Product map root must be a JSON object")

    artifact_ref = _verify_manifest_envelope(manifest)
    products = manifest.get("products")
    if not isinstance(products, list) or len(products) != _PRODUCT_COUNT:
        raise GuideClosedDemoProductMapError(f"Expected {_PRODUCT_COUNT} products in manifest")

    exact_mapping = _extract_exact_mapping(products)

    return GuideClosedDemoProductMap(
        artifact_ref=artifact_ref,
        product_count=_PRODUCT_COUNT,
        exact_mapping=exact_mapping,
    )
