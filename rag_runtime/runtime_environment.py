"""Canonical Runtime Environment vocabulary (PD-799-20260918, Issue #810)."""

from enum import StrEnum


class RuntimeEnvironmentCode(StrEnum):
    LOCAL = "LOCAL"
    TEST = "TEST"
    CLOSED_DEMO = "CLOSED_DEMO"
    PRODUCTION = "PRODUCTION"
