"""Deterministic, non-sensitive preflight verification for Protected Retrieval.

Verifies configuration fail-closed boundaries, database least-privilege connection
policies, and read-only GitHub approval repository/branch access.
Outputs strictly non-sensitive JSON without secrets, hostnames, or raw error messages.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from sqlalchemy.ext.asyncio import create_async_engine

from infra.python.protected_retrieval_role_policy import (
    validate_protected_control_connection,
    validate_protected_data_connection,
)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def validate_config_boundary(env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Validate protected configuration presence, separation, and fail-closed rules."""
    e = os.environ if env is None else env

    enabled = e.get("PROTECTED_RETRIEVAL_ENABLED", "").lower().strip()
    if enabled != "true":
        return False, "PROTECTED_RETRIEVAL_NOT_ENABLED"

    required_keys = (
        "PROTECTED_DB_HOST",
        "PROTECTED_DB_PORT",
        "PROTECTED_DB_NAME",
        "PROTECTED_DB_USER",
        "PROTECTED_DB_PASSWORD",
        "PROTECTED_DB_CONTROL_USER",
        "PROTECTED_DB_CONTROL_PASSWORD",
        "PROTECTED_DB_SCHEMA",
        "PROTECTED_DB_ACCESS_ROLE",
        "PROTECTED_DB_CONTROL_ROLE",
        "PROTECTED_APPROVAL_REPOSITORY",
        "PROTECTED_APPROVAL_BRANCH",
        "PROTECTED_APPROVAL_GITHUB_TOKEN",
    )
    for k in required_keys:
        val = e.get(k, "").strip()
        if not val:
            return False, f"MISSING_{k}"

    schema = e["PROTECTED_DB_SCHEMA"].strip()
    access = e["PROTECTED_DB_ACCESS_ROLE"].strip()
    control = e["PROTECTED_DB_CONTROL_ROLE"].strip()
    data_user = e["PROTECTED_DB_USER"].strip()
    control_user = e["PROTECTED_DB_CONTROL_USER"].strip()

    for identifier, label in (
        (schema, "SCHEMA"),
        (access, "ACCESS_ROLE"),
        (control, "CONTROL_ROLE"),
        (data_user, "DATA_USER"),
        (control_user, "CONTROL_USER"),
    ):
        if not _SAFE_IDENTIFIER.fullmatch(identifier):
            return False, f"INVALID_IDENTIFIER_{label}"

    all_roles = [access, control, data_user, control_user]
    app_user = e.get("DB_APP_USER", "").strip()
    if app_user:
        all_roles.append(app_user)

    if len(all_roles) != len(set(all_roles)):
        return False, "ROLE_IDENTITY_COLLISION"

    repo = e["PROTECTED_APPROVAL_REPOSITORY"].strip()
    if not _SAFE_REPO.fullmatch(repo):
        return False, "INVALID_APPROVAL_REPOSITORY_FORMAT"

    branch = e["PROTECTED_APPROVAL_BRANCH"].strip()
    if not branch or ".." in branch:
        return False, "INVALID_APPROVAL_BRANCH_FORMAT"

    return True, "PASS"


async def check_database_connections(
    env: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Verify DATA and CONTROL connections against exact role policies."""
    e = os.environ if env is None else env

    db_host = e.get("PROTECTED_DB_HOST", "").strip()
    db_port = e.get("PROTECTED_DB_PORT", "5432").strip()
    db_name = e.get("PROTECTED_DB_NAME", "").strip()
    schema = e.get("PROTECTED_DB_SCHEMA", "").strip()
    access_role = e.get("PROTECTED_DB_ACCESS_ROLE", "").strip()
    control_role = e.get("PROTECTED_DB_CONTROL_ROLE", "").strip()

    data_user = e.get("PROTECTED_DB_USER", "").strip()
    data_password = e.get("PROTECTED_DB_PASSWORD", "").strip()
    control_user = e.get("PROTECTED_DB_CONTROL_USER", "").strip()
    control_password = e.get("PROTECTED_DB_CONTROL_PASSWORD", "").strip()

    data_url = (
        f"postgresql+asyncpg://{quote_plus(data_user)}:{quote_plus(data_password)}"
        f"@{db_host}:{db_port}/{db_name}"
    )
    control_url = (
        f"postgresql+asyncpg://{quote_plus(control_user)}:{quote_plus(control_password)}"
        f"@{db_host}:{db_port}/{db_name}"
    )

    data_status = "FAIL"
    try:
        data_engine = create_async_engine(data_url)
        try:
            async with data_engine.connect() as conn:
                await validate_protected_data_connection(
                    conn,
                    schema=schema,
                    data_access=access_role,
                    control=control_role,
                )
            data_status = "PASS"
        finally:
            await data_engine.dispose()
    except Exception:
        data_status = "FAIL"

    control_status = "FAIL"
    try:
        control_engine = create_async_engine(control_url)
        try:
            async with control_engine.connect() as conn:
                await validate_protected_control_connection(
                    conn,
                    schema=schema,
                    data_access=access_role,
                    control=control_role,
                )
            control_status = "PASS"
        finally:
            await control_engine.dispose()
    except Exception:
        control_status = "FAIL"

    return data_status, control_status


def check_github_approval_access(
    env: dict[str, str] | None = None,
    http_requester: Callable[[str, dict[str, str]], tuple[int, dict[str, Any]]] | None = None,
) -> tuple[str, str, str]:
    """Verify read access to approval repository/branch and determine write scope.

    Returns:
        (repo_read_status, branch_read_status, token_write_boundary)
    """
    e = os.environ if env is None else env

    repo = e.get("PROTECTED_APPROVAL_REPOSITORY", "").strip()
    branch = e.get("PROTECTED_APPROVAL_BRANCH", "").strip()
    token = e.get("PROTECTED_APPROVAL_GITHUB_TOKEN", "").strip()

    if not repo or not branch or not token:
        return "FAIL", "FAIL", "UNVERIFIED"

    def default_http_request(url: str, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=10) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                return status, body
        except HTTPError as err:
            return err.code, {}
        except URLError:
            return 0, {}
        except Exception:
            return 0, {}

    requester = http_requester or default_http_request
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ah-05-04-protected-retrieval-preflight",
    }

    # 1. Repo read capability & write permission check
    repo_url = f"https://api.github.com/repos/{repo}"
    repo_status, repo_data = requester(repo_url, headers)
    repo_read = "PASS" if repo_status == 200 else "FAIL"

    token_write_boundary = "UNVERIFIED"
    if repo_status == 200 and isinstance(repo_data, dict):
        permissions = repo_data.get("permissions")
        if isinstance(permissions, dict):
            push_allowed = permissions.get("push")
            admin_allowed = permissions.get("admin")
            if push_allowed is False and admin_allowed is False:
                token_write_boundary = "PASS"
            elif push_allowed is True or admin_allowed is True:
                token_write_boundary = "FAIL"
            else:
                token_write_boundary = "UNVERIFIED"
        else:
            token_write_boundary = "UNVERIFIED"

    # 2. Branch read capability
    branch_url = f"https://api.github.com/repos/{repo}/branches/{quote_plus(branch)}"
    branch_status, _ = requester(branch_url, headers)
    branch_read = "PASS" if branch_status == 200 else "FAIL"

    return repo_read, branch_read, token_write_boundary


async def run_preflight(
    env: dict[str, str] | None = None,
    http_requester: Callable[[str, dict[str, str]], tuple[int, dict[str, Any]]] | None = None,
) -> dict[str, str]:
    """Execute all preflight checks and return non-sensitive status dictionary."""
    config_ok, _ = validate_config_boundary(env)
    config_status = "PASS" if config_ok else "FAIL"

    if config_ok:
        data_status, control_status = await check_database_connections(env)
        repo_read, branch_read, token_boundary = check_github_approval_access(env, http_requester)
    else:
        data_status = "FAIL"
        control_status = "FAIL"
        repo_read = "FAIL"
        branch_read = "FAIL"
        token_boundary = "UNVERIFIED"

    all_pass = (
        config_status == "PASS"
        and data_status == "PASS"
        and control_status == "PASS"
        and repo_read == "PASS"
        and branch_read == "PASS"
        and token_boundary == "PASS"
    )

    protected_runtime = "READY" if all_pass else "BLOCKED"

    return {
        "config_boundary": config_status,
        "data_connection": data_status,
        "control_connection": control_status,
        "approval_repository_read": repo_read,
        "approval_branch_read": branch_read,
        "approval_token_write_boundary": token_boundary,
        "protected_runtime": protected_runtime,
    }


def main() -> None:
    results = asyncio.run(run_preflight())
    sys.stdout.write(json.dumps(results, indent=2) + "\n")
    if results.get("protected_runtime") == "READY":
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
