#!/usr/bin/env python3

import argparse
import shlex
import sys
import tomllib
from pathlib import Path

import yaml  # type: ignore[import-untyped]

AUTO_COLLECTED_ROOTS = (
    Path("backend/app"),
    Path("tests/contract"),
    Path("tests/migration"),
    Path("tests/integration/rag"),
    Path("ai_worker/tests/core"),
    Path("ai_worker/tests/ocr"),
    Path("ai_worker/tests/rag"),
    Path("ai_worker/tests/evaluation"),
)

DEFAULT_INTEGRATION_FILES = (
    Path("tests/integration/test_worker_ocr_persistence.py"),
    Path("tests/integration/test_outbox_publisher.py"),
    Path("tests/integration/test_worker_job_execution_repository.py"),
    Path("tests/integration/test_worker_dlq_outbox_repository.py"),
    Path("tests/integration/test_worker_recovery_repository.py"),
)

# 전체 Integration CI 편입은 Issue #307에서 반복 안정성과 실행 시간을 확인한 뒤 결정합니다.
INTENTIONAL_OPT_IN_FILES = (
    Path("tests/integration/test_cors_and_errors.py"),
    Path("tests/integration/test_ocr_required_field_placeholder_e2e.py"),
    Path("tests/integration/test_redis_stream_adapter.py"),
    Path("tests/integration/test_worker_consumer_session_sharing.py"),
)

DEFAULT_EXECUTION_CONFIGS = (
    Path("scripts/ci/run_test.sh"),
    Path(".github/workflows/checks.yml"),
)
TEST_ENVIRONMENT_CONFIG = Path("scripts/ci/test_environment.sh")
TEST_ENVIRONMENT_WRAPPERS = (
    "run_with_backend_test_database",
    "run_with_worker_test_environment",
    "run_with_integration_test_environment",
)

INVENTORY_CHECKER = "scripts/ci/check_python_test_inventory.py"
PYTEST_CONFIG = Path("pyproject.toml")
ALTERNATIVE_PYTEST_CONFIGS = (Path("pytest.ini"), Path("tox.ini"), Path("setup.cfg"))

PYTEST_COMMAND_PREFIXES = {
    (),
    ("coverage", "run", "-m"),
    ("coverage", "run", "--append", "-m"),
    ("run_with_backend_test_database",),
    ("run_with_backend_test_database", "coverage", "run", "-m"),
    ("run_with_integration_test_environment", "coverage", "run", "--append", "-m"),
    ("run_with_worker_test_environment", "coverage", "run", "-m"),
    ("uv", "run"),
    ("uv", "run", "coverage", "run", "-m"),
    ("uv", "run", "coverage", "run", "--append", "-m"),
}

SAFE_PYTEST_FLAG_OPTIONS = {"-q", "-v"}
SAFE_PYTEST_OVERRIDE_PREFIXES = ("cache_dir=",)

IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
}


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _discover_python_tests(root: Path) -> set[Path]:
    discovered: set[Path] = set()
    for pattern in ("test_*.py", "*_test.py"):
        for path in root.rglob(pattern):
            relative_path = path.relative_to(root)
            if path.is_file() and not any(part in IGNORED_DIRECTORY_NAMES for part in relative_path.parts):
                discovered.add(relative_path)
    return discovered


def _classified_python_tests(discovered: set[Path]) -> set[Path]:
    classified = set(DEFAULT_INTEGRATION_FILES) | set(INTENTIONAL_OPT_IN_FILES)
    classified.update(
        path for path in discovered if any(_is_under(path, test_root) for test_root in AUTO_COLLECTED_ROOTS)
    )
    return classified


def _shell_command_tokens(config: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for line in config.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
        command: list[str] = []
        for token in tokens:
            if token in {";", "&", "&&", "||", "|"}:
                if command:
                    commands.append(command)
                    command = []
                continue
            command.append(token)
        if command:
            commands.append(command)
    return commands


def _yaml_step_run_blocks(config: str) -> list[str]:
    workflow = yaml.safe_load(config)
    if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs"), dict):
        return []

    blocks: list[str] = []
    for job in workflow["jobs"].values():
        if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
            continue
        for step in job["steps"]:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                blocks.append(step["run"])
    return blocks


def _execution_commands(config_path: Path, config: str) -> list[list[str]]:
    blocks = _yaml_step_run_blocks(config) if config_path.suffix in {".yml", ".yaml"} else [config]
    return [command for block in blocks for command in _shell_command_tokens(block)]


def _command_prefix(command: list[str], token_index: int) -> list[str]:
    prefix = command[:token_index]
    if prefix[:2] == ["if", "!"]:
        prefix = prefix[2:]
    elif prefix[:1] in (["if"], ["!"]):
        prefix = prefix[1:]
    return prefix


def _pytest_invocations(commands: list[list[str]]) -> list[list[str]]:
    invocations: list[list[str]] = []
    for command in commands:
        for index, token in enumerate(command):
            if token != "pytest":
                continue
            if tuple(_command_prefix(command, index)) not in PYTEST_COMMAND_PREFIXES:
                continue
            invocations.append([argument for argument in command[index + 1 :] if argument != "then"])
    return invocations


def _classify_pytest_arguments(arguments: list[str]) -> tuple[list[str], list[str]]:
    targets: list[str] = []
    unsupported_options: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in SAFE_PYTEST_FLAG_OPTIONS:
            index += 1
            continue
        if argument == "-o":
            if index + 1 >= len(arguments):
                unsupported_options.append(f"{argument} (missing value)")
                index += 1
            else:
                override = arguments[index + 1]
                if not override.startswith(SAFE_PYTEST_OVERRIDE_PREFIXES):
                    unsupported_options.append(f"{argument} {override}")
                index += 2
            continue
        if argument.startswith("-"):
            unsupported_options.append(argument)
        else:
            targets.append(argument)
        index += 1
    if unsupported_options:
        return [], unsupported_options
    return targets, []


def _runs_inventory_preflight(commands: list[list[str]]) -> bool:
    for command in commands:
        normalized = _command_prefix(command, len(command))
        if normalized[:2] == ["python", INVENTORY_CHECKER]:
            return True
        if normalized[:3] == ["uv", "run", "python"] and normalized[3:4] == [INVENTORY_CHECKER]:
            return True
    return False


def _pins_empty_pytest_addopts(commands: list[list[str]]) -> bool:
    return any(command == ["export", "PYTEST_ADDOPTS="] for command in commands)


def _pytest_addopts_command_errors(commands: list[list[str]], config_path: Path) -> list[str]:
    return [
        f"Command sets PYTEST_ADDOPTS in {config_path}: {' '.join(command)}"
        for command in commands
        if any(
            token == "PYTEST_ADDOPTS" or (token.startswith("PYTEST_ADDOPTS=") and token != "PYTEST_ADDOPTS=")
            for token in command
        )
    ]


def _workflow_pytest_addopts_errors(config: str) -> list[str]:
    workflow = yaml.safe_load(config)
    if not isinstance(workflow, dict):
        return ["GitHub Actions workflow is not a mapping"]

    errors: list[str] = []
    top_level_env = workflow.get("env")
    if not isinstance(top_level_env, dict) or top_level_env.get("PYTEST_ADDOPTS") != "":
        errors.append("GitHub Actions must set top-level env.PYTEST_ADDOPTS to an empty string")

    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                nested_path = f"{path}.{key}" if path else str(key)
                if key == "PYTEST_ADDOPTS" and nested_value != "":
                    errors.append(f"GitHub Actions sets non-empty {nested_path}")
                visit(nested_value, nested_path)
        elif isinstance(value, list):
            for index, nested_value in enumerate(value):
                visit(nested_value, f"{path}[{index}]")

    visit(workflow, "")
    return errors


def _validate_pytest_config(root: Path) -> list[str]:
    errors = [
        f"Alternative pytest config requires inventory review: {path}"
        for path in ALTERNATIVE_PYTEST_CONFIGS
        if (root / path).is_file()
    ]
    config_path = root / PYTEST_CONFIG
    if not config_path.is_file():
        return [*errors, f"Missing pytest config: {PYTEST_CONFIG}"]

    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    pytest_options = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    if isinstance(pytest_options, dict) and pytest_options.get("addopts"):
        errors.append(f"Pytest addopts may prevent automatic collection: {pytest_options['addopts']}")
    return errors


def _validate_test_environment(root: Path) -> list[str]:
    config_path = root / TEST_ENVIRONMENT_CONFIG
    if not config_path.is_file():
        return [f"Missing Python test environment config: {TEST_ENVIRONMENT_CONFIG}"]

    config = config_path.read_text(encoding="utf-8")
    errors: list[str] = []
    required_sequence = 'PYTEST_ADDOPTS= \\\n    uv run --env-file "$ENV_FILE"'
    for wrapper in TEST_ENVIRONMENT_WRAPPERS:
        start = config.find(f"{wrapper}() {{")
        end = config.find("\n}", start)
        if start < 0 or end < 0:
            errors.append(f"Missing test environment wrapper in {TEST_ENVIRONMENT_CONFIG}: {wrapper}")
            continue
        if required_sequence not in config[start:end]:
            errors.append(f"Test environment wrapper does not pin empty PYTEST_ADDOPTS before uv --env-file: {wrapper}")
    return errors


def _validate_declared_targets(root: Path) -> list[str]:
    errors: list[str] = []
    for path in AUTO_COLLECTED_ROOTS:
        if not (root / path).is_dir():
            errors.append(f"Configured Python test root is not a directory: {path}")
    for path in DEFAULT_INTEGRATION_FILES + INTENTIONAL_OPT_IN_FILES:
        if not (root / path).is_file():
            errors.append(f"Configured Python test file does not exist: {path}")
    return errors


def _validate_execution_configs(root: Path) -> list[str]:
    errors: list[str] = []
    required_targets = AUTO_COLLECTED_ROOTS + DEFAULT_INTEGRATION_FILES

    for config_path in DEFAULT_EXECUTION_CONFIGS:
        absolute_path = root / config_path
        if not absolute_path.is_file():
            errors.append(f"Missing Python test execution config: {config_path}")
            continue

        config = absolute_path.read_text(encoding="utf-8")
        commands = _execution_commands(config_path, config)
        errors.extend(_pytest_addopts_command_errors(commands, config_path))
        pytest_invocations = _pytest_invocations(commands)
        pytest_arguments = [argument for invocation in pytest_invocations for argument in invocation]
        classified_invocations = [_classify_pytest_arguments(invocation) for invocation in pytest_invocations]
        pytest_targets = [target for targets, _ in classified_invocations for target in targets]
        node_selections = sorted(argument for argument in pytest_arguments if "::" in argument)
        if node_selections:
            errors.append(
                "Node-specific pytest selections prevent automatic collection:\n"
                + "\n".join(f"  {config_path}: {selection}" for selection in node_selections)
            )

        unsupported_options = sorted(
            option for _, invocation_options in classified_invocations for option in invocation_options
        )
        if unsupported_options:
            errors.append(
                "Unsupported pytest options may prevent automatic collection:\n"
                + "\n".join(f"  {config_path}: {option}" for option in unsupported_options)
            )

        missing_targets = [str(path) for path in required_targets if str(path) not in pytest_targets]
        if missing_targets:
            errors.append(
                f"Default Python test targets missing from {config_path}:\n"
                + "\n".join(f"  {target}" for target in missing_targets)
            )

        unexpected_opt_in = [str(path) for path in INTENTIONAL_OPT_IN_FILES if str(path) in pytest_targets]
        if unexpected_opt_in:
            errors.append(
                f"Opt-in Python tests unexpectedly included by {config_path}:\n"
                + "\n".join(f"  {target}" for target in unexpected_opt_in)
            )

        if not _runs_inventory_preflight(commands):
            errors.append(f"Python test inventory preflight missing from {config_path}: {INVENTORY_CHECKER}")

        if config_path.suffix in {".yml", ".yaml"}:
            errors.extend(_workflow_pytest_addopts_errors(config))
        elif not _pins_empty_pytest_addopts(commands):
            errors.append(f"Inherited PYTEST_ADDOPTS is not pinned empty by {config_path}")

    return errors


def check_inventory(root: Path) -> list[str]:
    discovered = _discover_python_tests(root)
    unclassified = sorted(discovered - _classified_python_tests(discovered))
    errors: list[str] = []

    if unclassified:
        errors.append("Unclassified Python test files:\n" + "\n".join(f"  {path}" for path in unclassified))

    errors.extend(_validate_declared_targets(root))
    errors.extend(_validate_pytest_config(root))
    errors.extend(_validate_test_environment(root))
    errors.extend(_validate_execution_configs(root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate that every Python test has an explicit execution lane.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root to inspect")
    args = parser.parse_args()

    errors = check_inventory(args.root.resolve())
    if errors:
        print("\n\n".join(errors), file=sys.stderr)
        print(
            "Classify each file as a default lane target or an intentional opt-in test before merging.",
            file=sys.stderr,
        )
        return 1

    print("Python test inventory is fully classified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
