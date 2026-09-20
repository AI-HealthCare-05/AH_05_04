"""Contract tests for Issue #811: Protected Environment Provisioning & Isolation Boundaries."""

from __future__ import annotations

import argparse
import asyncio
import threading
from pathlib import Path

import pytest
import yaml

from ai_worker.admin.protected_retrieval_preflight import (
    check_github_approval_access,
    validate_config_boundary,
)
from infra.python import provision_protected_retrieval
from infra.python.provision_protected_retrieval import (
    validate_distinct_roles,
    validate_safe_identifier,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_workflow_trigger_and_environment_boundary() -> None:
    wf_path = REPO_ROOT / ".github/workflows/protected_retrieval_runner.yml"
    assert wf_path.is_file(), "protected_retrieval_runner.yml must exist"

    content = wf_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    # 1. Trigger constraints: workflow_dispatch ONLY
    triggers = data.get("on") or data.get(True)  # YAML parses 'on:' as boolean True in 1.1 if unquoted
    assert "workflow_dispatch" in triggers, "Workflow must trigger via workflow_dispatch"
    for forbidden in ("push", "pull_request", "schedule", "workflow_run"):
        assert forbidden not in triggers, f"Forbidden trigger {forbidden} in protected runner workflow"

    # 2. Secret value must NOT be accepted via workflow inputs
    inputs = triggers["workflow_dispatch"].get("inputs", {})
    for input_name in inputs:
        assert "token" not in input_name.lower(), f"Secrets cannot be inputs: {input_name}"
        assert "password" not in input_name.lower(), f"Secrets cannot be inputs: {input_name}"
        assert "secret" not in input_name.lower(), f"Secrets cannot be inputs: {input_name}"
        assert "key" not in input_name.lower(), f"Keys cannot be inputs: {input_name}"
        assert "known_hosts" not in input_name.lower(), f"Host keys cannot be inputs: {input_name}"

    # 3. Environment: protected-retrieval on all jobs
    jobs = data.get("jobs", {})
    assert "provision" in jobs, "provision job must exist"
    assert "preflight" in jobs, "preflight job must exist"

    assert jobs["provision"].get("environment") == "protected-retrieval"
    assert jobs["preflight"].get("environment") == "protected-retrieval"


def test_workflow_preflight_uses_read_only_job_scoped_github_token() -> None:
    wf_path = REPO_ROOT / ".github/workflows/protected_retrieval_runner.yml"
    content = wf_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    assert data.get("permissions") == {
        "contents": "read",
        "pull-requests": "read",
    }

    preflight_steps = data["jobs"]["preflight"]["steps"]
    preflight_env = next(
        step["env"] for step in preflight_steps if step.get("name") == "Execute remote preflight check"
    )
    assert preflight_env["PROTECTED_APPROVAL_GITHUB_TOKEN"] == "${{ github.token }}"
    assert "secrets.PROTECTED_APPROVAL_GITHUB_TOKEN" not in content


def test_workflow_ssh_host_identity_verification() -> None:
    wf_path = REPO_ROOT / ".github/workflows/protected_retrieval_runner.yml"
    assert wf_path.is_file(), "protected_retrieval_runner.yml must exist"

    content = wf_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    # 1. StrictHostKeyChecking=no must NOT exist in the workflow
    assert "StrictHostKeyChecking=no" not in content, (
        "StrictHostKeyChecking=no must not exist in protected_retrieval_runner.yml"
    )

    # 2. StrictHostKeyChecking=yes must be present
    assert "StrictHostKeyChecking=yes" in content, (
        "StrictHostKeyChecking=yes must be present in protected_retrieval_runner.yml"
    )

    # 3. Pinned known_hosts secret path exists and is used
    assert "secrets.EC2_SSH_KNOWN_HOSTS" in content, "Workflow must reference secrets.EC2_SSH_KNOWN_HOSTS"
    assert "~/.ssh/known_hosts" in content, "Workflow must write pinned host keys to ~/.ssh/known_hosts"

    # 4. UserKnownHostsFile is explicitly used
    assert "UserKnownHostsFile=" in content, "UserKnownHostsFile must be explicitly configured in SSH command"

    # 5. Host key is NOT accepted via workflow_dispatch inputs
    triggers = data.get("on") or data.get(True)
    inputs = triggers.get("workflow_dispatch", {}).get("inputs", {})
    for input_name in inputs:
        assert "host_key" not in input_name.lower(), f"Host key cannot be workflow input: {input_name}"
        assert "known_hosts" not in input_name.lower(), f"Known hosts cannot be workflow input: {input_name}"

    # Verify both provision and preflight jobs configure EC2_SSH_KNOWN_HOSTS
    jobs = data.get("jobs", {})
    for job_name in ("provision", "preflight"):
        job = jobs.get(job_name, {})
        steps = job.get("steps", [])
        env_vars: dict[str, str] = {}
        for step in steps:
            env_vars.update(step.get("env", {}))
        assert "EC2_SSH_KNOWN_HOSTS" in env_vars, f"{job_name} job must inject EC2_SSH_KNOWN_HOSTS secret"
        assert env_vars["EC2_SSH_KNOWN_HOSTS"] == "${{ secrets.EC2_SSH_KNOWN_HOSTS }}", (
            f"{job_name} job must reference secrets.EC2_SSH_KNOWN_HOSTS"
        )


def test_workflow_waits_for_exact_runner_ipv4_allowlist_before_each_ssh() -> None:
    wf_path = REPO_ROOT / ".github/workflows/protected_retrieval_runner.yml"
    data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))

    strict_ipv4_pattern = (
        r"^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}"
        r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$"
    )
    cleanup_notice = "Provision/preflight 종료 후 임시 TCP/22 /32 rule을 즉시 제거"

    for job_name in ("provision", "preflight"):
        run_script = data["jobs"][job_name]["steps"][1]["run"]
        first_ssh = run_script.index('\n$SSH_CMD "')

        assert 'RUNNER_PUBLIC_IPV4="$(curl -fsS https://api.ipify.org)"' in run_script[:first_ssh]
        assert strict_ipv4_pattern in run_script[:first_ssh]
        assert "::notice::RUNNER_PUBLIC_IPV4=${RUNNER_PUBLIC_IPV4}" in run_script[:first_ssh]
        assert "::notice::SG_TEMP_CIDR=${RUNNER_PUBLIC_IPV4}/32" in run_script[:first_ssh]
        assert f"::notice::{cleanup_notice}" in run_script[:first_ssh]
        assert "sleep 300" in run_script[:first_ssh]

        assert "0.0.0.0/0" not in run_script
        assert "aws ec2 authorize-security-group-ingress" not in run_script


def test_checks_yml_has_no_protected_secret_references() -> None:
    checks_path = REPO_ROOT / ".github/workflows/checks.yml"
    assert checks_path.is_file()
    content = checks_path.read_text(encoding="utf-8")
    assert "secrets.PROTECTED_" not in content, "checks.yml must not reference any PROTECTED_* secrets"
    assert "EC2_SSH_KNOWN_HOSTS" not in content, "checks.yml must not reference EC2_SSH_KNOWN_HOSTS"
    assert "EC2_SSH_KEY" not in content, "checks.yml must not reference EC2_SSH_KEY"
    assert "secrets.EC2_" not in content, "checks.yml must not reference any EC2_* secrets"


def test_compose_profiles_and_security_boundaries() -> None:
    compose_path = REPO_ROOT / "infra/docker/docker-compose.prod.yml"
    assert compose_path.is_file()
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data.get("services", {})

    # 1. Presence of one-shot services
    assert "protected-retrieval-provision" in services
    assert "protected-retrieval-runner" in services

    prov = services["protected-retrieval-provision"]
    runner = services["protected-retrieval-runner"]

    # 2. Profiles
    assert prov.get("profiles") == ["protected-retrieval-admin"]
    assert runner.get("profiles") == ["protected-runner"]

    # 3. Restart policy: 'no'
    assert str(prov.get("restart")).lower() in ("no", "false")
    assert str(runner.get("restart")).lower() in ("no", "false")

    # 4. No exposed ports
    assert not prov.get("ports"), "protected-retrieval-provision must not expose ports"
    assert not runner.get("ports"), "protected-retrieval-runner must not expose ports"

    # 5. No privileged mode, no docker.sock mount
    assert not prov.get("privileged", False)
    assert not runner.get("privileged", False)

    for svc in (prov, runner):
        volumes = svc.get("volumes", [])
        for vol in volumes:
            assert "docker.sock" not in str(vol), "docker.sock mounting forbidden"

    # 6. Network: ws
    assert prov.get("networks") == ["ws"]
    assert runner.get("networks") == ["ws"]

    # 7. Credential separation
    prov_env = prov.get("environment", {})
    runner_env = runner.get("environment", {})

    # Provision cannot receive GitHub token
    assert "PROTECTED_APPROVAL_GITHUB_TOKEN" not in prov_env

    # Runner cannot receive DB admin / migration credentials
    assert "DB_ADMIN_USER" not in runner_env
    assert "DB_ADMIN_PASSWORD" not in runner_env
    assert "DB_MIGRATION_USER" not in runner_env
    assert "DB_MIGRATION_PASSWORD" not in runner_env

    # General services must not have protected credentials
    general_services = ("fastapi", "ai-worker", "migrate", "nginx", "source-management", "source-writer")
    for name in general_services:
        svc_env = services[name].get("environment", {})
        assert "PROTECTED_DB_PASSWORD" not in svc_env, f"{name} must not have PROTECTED_DB_PASSWORD"
        assert "PROTECTED_DB_CONTROL_PASSWORD" not in svc_env, f"{name} must not have CONTROL_PASSWORD"
        assert "PROTECTED_APPROVAL_GITHUB_TOKEN" not in svc_env, f"{name} must not have GITHUB_TOKEN"


def test_backend_dockerfile_packages_protected_migrations() -> None:
    dockerfile_path = REPO_ROOT / "backend/app/Dockerfile"
    assert dockerfile_path.is_file()
    content = dockerfile_path.read_text(encoding="utf-8")
    assert "COPY ./infra/protected_retrieval ./infra/protected_retrieval" in content


def test_provisioning_script_prohibits_direct_data_mutation() -> None:
    prov_path = REPO_ROOT / "infra/python/provision_protected_retrieval.py"
    assert prov_path.is_file()
    content = prov_path.read_text(encoding="utf-8")

    # Prohibit direct application data manipulation
    assert "INSERT INTO protected_identity" not in content
    assert "INSERT INTO protected_dataset" not in content
    assert "INSERT INTO approval_evidence" not in content
    assert "INSERT INTO authorization_grant" not in content


def test_provisioning_role_distinctness() -> None:
    # Disallow duplicates
    with pytest.raises(ValueError, match="Database roles must be strictly distinct"):
        validate_distinct_roles("owner", "access", "control", "access", "user")

    # Identifiers must be safe
    with pytest.raises(ValueError, match="must be a safe PostgreSQL identifier"):
        validate_safe_identifier("bad;role", "ROLE")


async def test_protected_migrations_run_outside_the_running_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MigrationReachedError(RuntimeError):
        pass

    event_loop_thread = threading.get_ident()
    migration_threads: list[int] = []

    def migration_probe(**_: object) -> None:
        migration_threads.append(threading.get_ident())
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        raise MigrationReachedError

    environment = {
        "DB_HOST": "postgres",
        "DB_PORT": "5432",
        "DB_NAME": "protected_test",
        "DB_ADMIN_USER": "admin_user",
        "DB_ADMIN_PASSWORD": "admin_password",
        "PROTECTED_DB_SCHEMA": "protected_eval",
        "PROTECTED_DB_OWNER_ROLE": "protected_owner",
        "PROTECTED_DB_ACCESS_ROLE": "protected_access",
        "PROTECTED_DB_CONTROL_ROLE": "protected_control",
        "PROTECTED_DB_USER": "protected_data_login",
        "PROTECTED_DB_PASSWORD": "data_password",
        "PROTECTED_DB_CONTROL_USER": "protected_control_login",
        "PROTECTED_DB_CONTROL_PASSWORD": "control_password",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(provision_protected_retrieval, "run_protected_migrations", migration_probe)

    with pytest.raises(MigrationReachedError):
        await provision_protected_retrieval.async_main(argparse.Namespace(provision=True, verify_only=False))

    assert migration_threads
    assert migration_threads[0] != event_loop_thread


def test_preflight_config_boundary_validation() -> None:
    # 1. Disabled retrieval fails cleanly
    ok, reason = validate_config_boundary({"PROTECTED_RETRIEVAL_ENABLED": "false"})
    assert not ok
    assert reason == "PROTECTED_RETRIEVAL_NOT_ENABLED"

    # 2. Missing fields fail
    ok, reason = validate_config_boundary({"PROTECTED_RETRIEVAL_ENABLED": "true"})
    assert not ok
    assert reason.startswith("MISSING_")

    # 3. Collision between DATA and CONTROL users
    base_env = {
        "PROTECTED_RETRIEVAL_ENABLED": "true",
        "PROTECTED_DB_HOST": "postgres",
        "PROTECTED_DB_PORT": "5432",
        "PROTECTED_DB_NAME": "test_db",
        "PROTECTED_DB_USER": "shared_user",
        "PROTECTED_DB_PASSWORD": "data_password",
        "PROTECTED_DB_CONTROL_USER": "shared_user",
        "PROTECTED_DB_CONTROL_PASSWORD": "control_password",
        "PROTECTED_DB_SCHEMA": "protected_eval",
        "PROTECTED_DB_ACCESS_ROLE": "protected_eval_data_role",
        "PROTECTED_DB_CONTROL_ROLE": "protected_eval_control_role",
        "PROTECTED_APPROVAL_REPOSITORY": "AI-HealthCare-05/AH_05_04",
        "PROTECTED_APPROVAL_BRANCH": "main",
        "PROTECTED_APPROVAL_GITHUB_TOKEN": "token_val",
    }
    ok, reason = validate_config_boundary(base_env)
    assert not ok
    assert reason == "ROLE_IDENTITY_COLLISION"

    # 4. Valid distinct configuration passes
    valid_env = dict(base_env)
    valid_env["PROTECTED_DB_CONTROL_USER"] = "distinct_control_user"
    ok, reason = validate_config_boundary(valid_env)
    assert ok
    assert reason == "PASS"


def test_preflight_github_approval_access_check() -> None:
    env = {
        "PROTECTED_APPROVAL_REPOSITORY": "org/repo",
        "PROTECTED_APPROVAL_BRANCH": "main",
        "PROTECTED_APPROVAL_GITHUB_TOKEN": "dummy_token",
    }

    # Case A: 200 OK with push=False, admin=False -> repo PASS, branch PASS, write PASS
    def mock_http_readonly(url: str, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
        if "branches" in url:
            return 200, {"name": "main"}
        return 200, {"permissions": {"push": False, "admin": False, "pull": True}}

    r_read, b_read, w_bound = check_github_approval_access(env, mock_http_readonly)
    assert r_read == "PASS"
    assert b_read == "PASS"
    assert w_bound == "PASS"

    # Case B: 200 OK with push=True -> repo PASS, branch PASS, write FAIL
    def mock_http_writable(url: str, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
        if "branches" in url:
            return 200, {"name": "main"}
        return 200, {"permissions": {"push": True, "admin": False, "pull": True}}

    r_read, b_read, w_bound = check_github_approval_access(env, mock_http_writable)
    assert r_read == "PASS"
    assert b_read == "PASS"
    assert w_bound == "FAIL"

    # Case C: Fine-grained token with no permissions block -> UNVERIFIED
    def mock_http_unverified(url: str, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
        if "branches" in url:
            return 200, {"name": "main"}
        return 200, {}

    r_read, b_read, w_bound = check_github_approval_access(env, mock_http_unverified)
    assert r_read == "PASS"
    assert b_read == "PASS"
    assert w_bound == "UNVERIFIED"
