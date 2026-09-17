from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = (
    "load_tests/locustfile.py",
    "load_tests/README.md",
    "docs/testing/load-testing-627.md",
)

REQUIRED_SNIPPETS = {
    "load_tests/locustfile.py": (
        'DEFAULT_SMOKE_PATH = "/api/openapi.json"',
        "LOAD_TEST_BEARER_TOKEN",
        "framework-smoke",
    ),
    "load_tests/README.md": (
        "uvx locust",
        "API-specific scenarios",
        "Do not use `/api/v1/health`",
    ),
    "docs/testing/load-testing-627.md": (
        "#627",
        "API별 시나리오",
        "Production capacity claim",
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
