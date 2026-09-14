#!/usr/bin/env python3

import os
import sys

ALWAYS_REQUIRED_JOBS = ("classifier", "inventory")
SELECTIVE_JOBS = ("migration", "backend", "rag", "contract", "worker")


def _environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def _job_error(name: str, required: bool, result: str) -> str | None:
    if result == "success":
        return None
    if not required and result == "skipped":
        return None
    return f"{name}={result} (required={str(required).lower()})"


def main() -> int:
    errors: list[str] = []
    try:
        for name in ALWAYS_REQUIRED_JOBS:
            result = _environment(f"{name.upper()}_RESULT")
            error = _job_error(name, True, result)
            if error:
                errors.append(error)

        for name in SELECTIVE_JOBS:
            required_value = _environment(f"{name.upper()}_REQUIRED")
            if required_value not in {"true", "false"}:
                raise ValueError(f"Invalid {name.upper()}_REQUIRED value: {required_value}")
            required = required_value == "true"
            result = _environment(f"{name.upper()}_RESULT")
            error = _job_error(name, required, result)
            if error:
                errors.append(error)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    if errors:
        print("Python test job gate failed: " + ", ".join(errors), file=sys.stderr)
        return 1

    print("Python test jobs satisfied the selected CI scope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
