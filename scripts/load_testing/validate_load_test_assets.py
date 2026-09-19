from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = (
    "load_tests/locustfile.py",
    "load_tests/auth_smoke.py",
    "load_tests/ocr_worker_smoke.py",
    "load_tests/schedule_smoke.py",
    "load_tests/README.md",
    "docs/testing/load-testing-627.md",
)

REQUIRED_SNIPPETS = {
    "load_tests/locustfile.py": (
        'DEFAULT_SMOKE_PATH = "/api/openapi.json"',
        "LOAD_TEST_BEARER_TOKEN",
        "framework-smoke",
    ),
    "load_tests/auth_smoke.py": (
        'LOGIN_PATH = "/api/v1/auth/login"',
        'TOKEN_REFRESH_PATH = "/api/v1/auth/token/refresh"',
        'USER_ME_PATH = "/api/v1/users/me"',
        "LOAD_TEST_AUTH_PASSWORD",
        "LOAD_TEST_AUTH_INCLUDE_REFRESH",
    ),
    "load_tests/ocr_worker_smoke.py": (
        "OcrWorkerSmokeUser",
        "ocr-smoke:create-ocr-job",
        "LOAD_TEST_OCR_MAX_WAIT_SECONDS",
        "ai-one-cycle-clova-openai-v1.json",
    ),
    "load_tests/schedule_smoke.py": (
        'LOGIN_PATH = "/api/v1/auth/login"',
        'MEDICATION_OCCURRENCES_PATH = "/api/v1/medication-occurrences"',
        "LOAD_TEST_SCHEDULE_DATE",
        "LOAD_TEST_SCHEDULE_DETAIL_LIMIT",
        "schedule-smoke:occurrence-medication",
    ),
    "load_tests/README.md": (
        "uvx locust",
        "Auth Baseline Smoke Command",
        "LOAD_TEST_AUTH_INCLUDE_REFRESH=true",
        "Medication schedule read smoke",
        "LOAD_TEST_SCHEDULE_DATE",
        "LOAD_TEST_SCHEDULE_DETAIL_LIMIT=0",
        "Medication Schedule Read Smoke Command",
        "API-specific scenarios",
        "OCR / Worker Smoke Command",
        "Do not use `/api/v1/health`",
    ),
    "docs/testing/load-testing-627.md": (
        "#627",
        "API별 시나리오",
        "OCR / Worker Minimum Smoke",
        "Production capacity claim",
        "LOAD_TEST_AUTH_INCLUDE_REFRESH=true",
        "Medication schedule read smoke",
        "LOAD_TEST_SCHEDULE_DATE",
        "LOAD_TEST_SCHEDULE_DETAIL_LIMIT=0",
    ),
}

FORBIDDEN_SNIPPETS = (
    "OPENAI_API_KEY=",
    "CLOVA_OCR_SECRET=",
    "LOAD_TEST_BEARER_TOKEN=ey",
    "password=",
)


def validate_assets(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for relative_path in REQUIRED_FILES:
        path = root / relative_path
        if not path.is_file():
            errors.append(f"missing required file: {relative_path}")
            continue
        text = path.read_text(encoding="utf-8")
        for snippet in REQUIRED_SNIPPETS.get(relative_path, ()):
            if snippet not in text:
                errors.append(f"{relative_path} is missing snippet: {snippet}")
        for forbidden in FORBIDDEN_SNIPPETS:
            if forbidden in text:
                errors.append(f"{relative_path} contains forbidden sensitive-looking snippet: {forbidden}")
    return errors


def main() -> int:
    errors = validate_assets()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("load-test assets are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
