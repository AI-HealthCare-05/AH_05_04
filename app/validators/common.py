from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import AfterValidator

T = TypeVar("T")


def optional_after_validator(func: Callable[..., Any]) -> AfterValidator:
    def _validate(v: T | None) -> T | None:
        return func(v) if v is not None else v

    return AfterValidator(_validate)
