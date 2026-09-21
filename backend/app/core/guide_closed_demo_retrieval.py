"""Dedicated read-only retrieval composition façade for Guide CLOSED_DEMO.

This module re-exports the Guide retrieval service, evidence types, and errors from
the approved CLOSED_DEMO retrieval composition façade (app.core.closed_demo_retrieval),
ensuring zero direct ai_worker imports in backend production code.
"""

from __future__ import annotations

from app.core.closed_demo_retrieval import (
    ClosedDemoRetrievalDatabaseConfig,
    ClosedDemoRetrievalDependencies,
    GuideClosedDemoEvidence,
    GuideClosedDemoEvidenceFilteringError,
    GuideClosedDemoRetrievalConfigurationError,
    GuideClosedDemoRetrievalExecutionError,
    GuideClosedDemoRetrievalService,
    build_closed_demo_retrieval_dependencies,
    build_guide_closed_demo_retrieval_service,
)

__all__ = [
    "ClosedDemoRetrievalDatabaseConfig",
    "ClosedDemoRetrievalDependencies",
    "GuideClosedDemoEvidence",
    "GuideClosedDemoEvidenceFilteringError",
    "GuideClosedDemoRetrievalConfigurationError",
    "GuideClosedDemoRetrievalExecutionError",
    "GuideClosedDemoRetrievalService",
    "build_closed_demo_retrieval_dependencies",
    "build_guide_closed_demo_retrieval_service",
]
