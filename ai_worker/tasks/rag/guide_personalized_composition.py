"""Composition-only boundary for one approved Guide generation request."""

from __future__ import annotations

from ai_worker.tasks.rag.guideline_generator import (
    GuidelineGenerationRequest,
    GuidelineGenerationResult,
    GuidelineGeneratorPort,
)

__all__ = ["compose_personalized_guide"]


async def compose_personalized_guide(
    request: GuidelineGenerationRequest,
    *,
    generator: GuidelineGeneratorPort,
) -> GuidelineGenerationResult:
    """Invoke the provider exactly once without selecting, validating, or mapping."""
    return await generator.generate(request)
