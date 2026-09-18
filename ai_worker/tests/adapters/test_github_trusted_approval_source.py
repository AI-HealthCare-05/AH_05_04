from __future__ import annotations

import base64
from hashlib import sha256

import httpx
import pytest
from pydantic import SecretStr

from ai_worker.adapters.github_trusted_approval_source import GitHubTrustedApprovalSource
from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    AuthorizationAuditAction,
    ProtectedAction,
    ProtectedApprovalRole,
    ProtectedSecurityError,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import (
    ApprovalSourceNotFoundError,
    C1ApprovalArtifact,
    FreezeApprovalArtifact,
    FreezeApprovalLocator,
    c1_approval_artifact_path,
    compute_approval_canonical_raw_sha256,
    freeze_approval_artifact_path,
)

REPO = "AI-HealthCare-05/AH_05_04"
DEFAULT_BRANCH = "develop"
TOKEN = "ghp_secret_test_token_12345"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
UUID_FREEZE = "123e4567-e89b-42d3-a456-426614174000"
UUID_DATASET = "123e4567-e89b-42d3-a456-426614174001"


def _make_c1_artifact_bytes(
    source_event_id: str,
    target_commit_oid: str = COMMIT_A,
    target_artifact_sha256: str = SHA_B,
) -> bytes:
    artifact = C1ApprovalArtifact(
        source_event_id=source_event_id,
        authorization_action=AuthorizationAuditAction.GRANT,
        approved_grant_payload_sha256=SHA_A,
        issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
        target_commit_oid=target_commit_oid,
        target_artifact_sha256=target_artifact_sha256,
        implementation_participants=(
            ActorIdentity(namespace="GITHUB_LOGIN", actor_id="alice"),
            ActorIdentity(namespace="GITHUB_LOGIN", actor_id="bob"),
        ),
    )
    return artifact.model_dump_json().encode("utf-8")


def _make_freeze_artifact_bytes(
    source_event_id: str,
    target_commit_oid: str = COMMIT_A,
    target_artifact_sha256: str = SHA_C,
) -> bytes:
    artifact = FreezeApprovalArtifact(
        source_event_id=source_event_id,
        dataset_id=UUID_DATASET,
        dataset_version="1.0.0",
        manifest_sha256=SHA_A,
        protected_artifact_sha256=SHA_B,
        authored_count=40,
        review_complete=True,
        leakage_axis_intersections=(0, 0, 0, 0),
        issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
        target_commit_oid=target_commit_oid,
        target_artifact_sha256=target_artifact_sha256,
        implementation_participants=(ActorIdentity(namespace="GITHUB_LOGIN", actor_id="alice"),),
    )
    return artifact.model_dump_json().encode("utf-8")


def test_init_validation() -> None:
    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=SecretStr(TOKEN),
    )
    assert source._repository == REPO
    assert source._default_branch == DEFAULT_BRANCH
    assert source._token == TOKEN

    with pytest.raises(ValueError):
        GitHubTrustedApprovalSource(repository="invalid", default_branch=DEFAULT_BRANCH, token=TOKEN)

    with pytest.raises(ValueError):
        GitHubTrustedApprovalSource(repository="org/", default_branch=DEFAULT_BRANCH, token=TOKEN)

    with pytest.raises(ValueError):
        GitHubTrustedApprovalSource(repository=REPO, default_branch="", token=TOKEN)

    with pytest.raises(ValueError):
        GitHubTrustedApprovalSource(repository=REPO, default_branch=DEFAULT_BRANCH, token="")


@pytest.mark.asyncio
async def test_c1_happy_path() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"
    artifact_bytes = _make_c1_artifact_bytes(source_event_id, target_commit_oid=COMMIT_A)
    blob_b64 = base64.b64encode(artifact_bytes).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={
                    "base": {"repo": {"full_name": REPO}},
                    "head": {"repo": {"full_name": REPO}},
                },
            )
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
            return httpx.Response(
                200,
                json={
                    "state": "APPROVED",
                    "commit_id": COMMIT_A,
                    "submitted_at": "2026-09-18T10:00:00Z",
                    "user": {"login": "reviewer_alice"},
                },
            )
        if url.endswith(f"/repos/AI-HealthCare-05/AH_05_04/compare/develop...{COMMIT_A}"):
            return httpx.Response(200, json={"status": "behind", "ahead_by": 0})
        if f"/repos/AI-HealthCare-05/AH_05_04/contents/{c1_approval_artifact_path(772, 999)}?ref={COMMIT_A}" in url:
            return httpx.Response(200, json={"content": blob_b64, "encoding": "base64"})
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=transport,
    )

    evidence = await source.fetch(source_event_id)
    assert evidence.source_event_id == source_event_id
    assert evidence.authorization_action is AuthorizationAuditAction.GRANT
    assert evidence.state == "APPROVED"
    assert evidence.issuer.actor.actor_id == "reviewer_alice"
    assert evidence.issuer.role is ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER
    assert evidence.target_commit_oid == COMMIT_A
    assert evidence.target_artifact_sha256 == SHA_B

    expected_canonical_raw = compute_approval_canonical_raw_sha256(
        review_id=999,
        state="APPROVED",
        submitted_at="2026-09-18T10:00:00Z",
        commit_id=COMMIT_A,
        reviewer_actor_id="reviewer_alice",
        repository=REPO,
        pull_number=772,
        approval_artifact_sha256=sha256(artifact_bytes).hexdigest(),
    )
    assert evidence.canonical_raw_sha256 == expected_canonical_raw


@pytest.mark.asyncio
async def test_freeze_happy_path_with_locator() -> None:
    source_event_id = UUID_FREEZE
    locator = FreezeApprovalLocator(
        source_event_id=source_event_id,
        pull_number=772,
        review_id=999,
    )
    artifact_bytes = _make_freeze_artifact_bytes(source_event_id, target_commit_oid=COMMIT_A)
    blob_b64 = base64.b64encode(artifact_bytes).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={
                    "base": {"repo": {"full_name": REPO}},
                    "head": {"repo": {"full_name": REPO}},
                },
            )
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
            return httpx.Response(
                200,
                json={
                    "state": "APPROVED",
                    "commit_id": COMMIT_A,
                    "submitted_at": "2026-09-18T11:00:00Z",
                    "user": {"login": "reviewer_bob"},
                },
            )
        if url.endswith(f"/repos/AI-HealthCare-05/AH_05_04/compare/develop...{COMMIT_A}"):
            return httpx.Response(200, json={"status": "identical", "ahead_by": 0})
        if (
            f"/repos/AI-HealthCare-05/AH_05_04/contents/{freeze_approval_artifact_path(source_event_id)}?ref={COMMIT_A}"
            in url
        ):
            return httpx.Response(200, json={"content": blob_b64, "encoding": "base64"})
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=transport,
    )

    evidence = await source.fetch_freeze(source_event_id, locator=locator)
    assert evidence.source_event_id == source_event_id
    assert evidence.action is ProtectedAction.FREEZE
    assert evidence.dataset_id == UUID_DATASET
    assert evidence.authored_count == 40
    assert evidence.review_complete is True
    assert evidence.issuer.actor.actor_id == "reviewer_bob"
    assert evidence.issuer.role is ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER
    assert evidence.target_commit_oid == COMMIT_A
    assert evidence.target_artifact_sha256 == SHA_C


@pytest.mark.asyncio
async def test_freeze_without_locator_raises_not_found() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"full_name": REPO}))
    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=transport,
    )
    with pytest.raises(ApprovalSourceNotFoundError):
        await source.fetch_freeze(UUID_FREEZE, locator=None)


@pytest.mark.asyncio
async def test_freeze_locator_mismatch_raises_not_found() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"full_name": REPO}))
    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=transport,
    )
    other_uuid = "123e4567-e89b-42d3-a456-426614174999"
    locator = FreezeApprovalLocator(source_event_id=other_uuid, pull_number=1, review_id=1)
    with pytest.raises(ApprovalSourceNotFoundError):
        await source.fetch_freeze(UUID_FREEZE, locator=locator)


@pytest.mark.asyncio
async def test_c1_malformed_source_string_raises_not_found() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"full_name": REPO}))
    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=transport,
    )
    with pytest.raises(ApprovalSourceNotFoundError):
        await source.fetch("malformed-source-id")


@pytest.mark.asyncio
async def test_c1_mismatched_repo_raises_not_found() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"full_name": REPO}))
    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=transport,
    )
    with pytest.raises(ApprovalSourceNotFoundError):
        await source.fetch("github:other-org/other-repo:pull:1:review:1")


@pytest.mark.asyncio
async def test_review_state_not_approved_raises_not_found() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
            )
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
            return httpx.Response(
                200,
                json={
                    "state": "CHANGES_REQUESTED",
                    "commit_id": COMMIT_A,
                    "submitted_at": "2026-09-18T10:00:00Z",
                    "user": {"login": "reviewer_alice"},
                },
            )
        return httpx.Response(404)

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApprovalSourceNotFoundError):
        await source.fetch(source_event_id)


@pytest.mark.asyncio
async def test_pr_404_capability_unavailable_raises_internal_error() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if "/repos/AI-HealthCare-05/AH_05_04/pulls?per_page=1" in url:
            # PR capability is unavailable (e.g. 403 Forbidden or 404)
            return httpx.Response(403, json={"message": "Forbidden"})
        return httpx.Response(404, json={"message": "Not Found"})

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProtectedSecurityError) as exc_info:
        await source.fetch(source_event_id)
    assert exc_info.value.reason_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_pr_404_healthy_capability_raises_not_found() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if "/repos/AI-HealthCare-05/AH_05_04/pulls?per_page=1" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not Found"})

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApprovalSourceNotFoundError):
        await source.fetch(source_event_id)


@pytest.mark.asyncio
async def test_review_404_with_healthy_pr_raises_not_found() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
            )
        return httpx.Response(404, json={"message": "Not Found"})

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApprovalSourceNotFoundError):
        await source.fetch(source_event_id)


@pytest.mark.asyncio
async def test_review_404_with_inaccessible_pr_raises_internal_error() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"
    pr_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal pr_calls
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            pr_calls += 1
            if pr_calls == 1:
                return httpx.Response(
                    200,
                    json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
                )
            # Second call (probing after review 404) fails
            return httpx.Response(403, json={"message": "Forbidden"})
        return httpx.Response(404, json={"message": "Not Found"})

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProtectedSecurityError) as exc_info:
        await source.fetch(source_event_id)
    assert exc_info.value.reason_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_artifact_404_with_accessible_commit_and_contents_raises_not_found() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
            )
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
            return httpx.Response(
                200,
                json={
                    "state": "APPROVED",
                    "commit_id": COMMIT_A,
                    "submitted_at": "2026-09-18T10:00:00Z",
                    "user": {"login": "reviewer_alice"},
                },
            )
        if f"/repos/AI-HealthCare-05/AH_05_04/compare/develop...{COMMIT_A}" in url:
            return httpx.Response(200, json={"status": "behind", "ahead_by": 0})
        if url.endswith(f"/repos/AI-HealthCare-05/AH_05_04/commits/{COMMIT_A}"):
            return httpx.Response(200, json={"sha": COMMIT_A})
        if f"/repos/AI-HealthCare-05/AH_05_04/contents?ref={COMMIT_A}" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not Found"})

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApprovalSourceNotFoundError):
        await source.fetch(source_event_id)


@pytest.mark.asyncio
async def test_artifact_404_with_inaccessible_commit_raises_internal_error() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
            )
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
            return httpx.Response(
                200,
                json={
                    "state": "APPROVED",
                    "commit_id": COMMIT_A,
                    "submitted_at": "2026-09-18T10:00:00Z",
                    "user": {"login": "reviewer_alice"},
                },
            )
        if f"/repos/AI-HealthCare-05/AH_05_04/compare/develop...{COMMIT_A}" in url:
            return httpx.Response(200, json={"status": "behind", "ahead_by": 0})
        # commits probe returns 404 (inaccessible commit)
        if url.endswith(f"/repos/AI-HealthCare-05/AH_05_04/commits/{COMMIT_A}"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(404, json={"message": "Not Found"})

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProtectedSecurityError) as exc_info:
        await source.fetch(source_event_id)
    assert exc_info.value.reason_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_artifact_404_with_inaccessible_contents_capability_raises_internal_error() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
            )
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
            return httpx.Response(
                200,
                json={
                    "state": "APPROVED",
                    "commit_id": COMMIT_A,
                    "submitted_at": "2026-09-18T10:00:00Z",
                    "user": {"login": "reviewer_alice"},
                },
            )
        if f"/repos/AI-HealthCare-05/AH_05_04/compare/develop...{COMMIT_A}" in url:
            return httpx.Response(200, json={"status": "behind", "ahead_by": 0})
        if url.endswith(f"/repos/AI-HealthCare-05/AH_05_04/commits/{COMMIT_A}"):
            return httpx.Response(200, json={"sha": COMMIT_A})
        if f"/repos/AI-HealthCare-05/AH_05_04/contents?ref={COMMIT_A}" in url:
            # Contents capability fails with 403
            return httpx.Response(403, json={"message": "Forbidden"})
        return httpx.Response(404, json={"message": "Not Found"})

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProtectedSecurityError) as exc_info:
        await source.fetch(source_event_id)
    assert exc_info.value.reason_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_ancestry_failure_raises_internal_error() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    for compare_status in (401, 403, 404, 500):

        def handler(
            request: httpx.Request,
            status: int = compare_status,
        ) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
                return httpx.Response(
                    200,
                    json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
                )
            if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
                return httpx.Response(
                    200,
                    json={
                        "state": "APPROVED",
                        "commit_id": COMMIT_A,
                        "submitted_at": "2026-09-18T10:00:00Z",
                        "user": {"login": "reviewer_alice"},
                    },
                )
            if f"/repos/AI-HealthCare-05/AH_05_04/compare/develop...{COMMIT_A}" in url:
                return httpx.Response(status, json={"message": "Error"})
            return httpx.Response(404)

        source = GitHubTrustedApprovalSource(
            repository=REPO,
            default_branch=DEFAULT_BRANCH,
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProtectedSecurityError) as exc_info:
            await source.fetch(source_event_id)
        assert exc_info.value.reason_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_provider_errors_raise_internal_error_and_redact_secrets() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    for status_code in (401, 403, 429, 500, 502):
        secret_leak = f"leak_sensitive_info_{status_code}"

        def handler(
            request: httpx.Request,
            code: int = status_code,
            leak: str = secret_leak,
        ) -> httpx.Response:
            del request
            return httpx.Response(code, text=f"Error with {leak} and token {TOKEN}")

        source = GitHubTrustedApprovalSource(
            repository=REPO,
            default_branch=DEFAULT_BRANCH,
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProtectedSecurityError) as exc_info:
            await source.fetch(source_event_id)
        assert exc_info.value.reason_code == "INTERNAL_ERROR"
        # Secret redaction check
        assert TOKEN not in str(exc_info.value)
        assert secret_leak not in str(exc_info.value)


@pytest.mark.asyncio
async def test_timeout_raises_internal_error() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Request timed out")

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProtectedSecurityError) as exc_info:
        await source.fetch(source_event_id)
    assert exc_info.value.reason_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_fork_pr_rejected() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={
                    "base": {"repo": {"full_name": REPO}},
                    "head": {"repo": {"full_name": "attacker-fork/AH_05_04"}},
                },
            )
        return httpx.Response(404)

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProtectedSecurityError) as exc_info:
        await source.fetch(source_event_id)
    assert exc_info.value.reason_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_ancestry_verification() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"

    # Diverged commit / ahead commit -> rejected
    for compare_response in (
        {"status": "ahead", "ahead_by": 1},
        {"status": "diverged", "ahead_by": 2},
    ):

        def handler(
            request: httpx.Request,
            comp_resp: dict[str, object] = compare_response,
        ) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
                return httpx.Response(200, json={"full_name": REPO})
            if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
                return httpx.Response(
                    200,
                    json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
                )
            if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
                return httpx.Response(
                    200,
                    json={
                        "state": "APPROVED",
                        "commit_id": COMMIT_A,
                        "submitted_at": "2026-09-18T10:00:00Z",
                        "user": {"login": "reviewer_alice"},
                    },
                )
            if f"/repos/AI-HealthCare-05/AH_05_04/compare/develop...{COMMIT_A}" in url:
                return httpx.Response(200, json=comp_resp)
            return httpx.Response(404)

        source = GitHubTrustedApprovalSource(
            repository=REPO,
            default_branch=DEFAULT_BRANCH,
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProtectedSecurityError) as exc_info:
            await source.fetch(source_event_id)
        assert exc_info.value.reason_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_artifact_target_commit_mismatch_rejected() -> None:
    source_event_id = "github:AI-HealthCare-05/AH_05_04:pull:772:review:999"
    # Artifact declares COMMIT_B while review was submitted on COMMIT_A
    artifact_bytes = _make_c1_artifact_bytes(source_event_id, target_commit_oid=COMMIT_B)
    blob_b64 = base64.b64encode(artifact_bytes).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04"):
            return httpx.Response(200, json={"full_name": REPO})
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772"):
            return httpx.Response(
                200,
                json={"base": {"repo": {"full_name": REPO}}, "head": {"repo": {"full_name": REPO}}},
            )
        if url.endswith("/repos/AI-HealthCare-05/AH_05_04/pulls/772/reviews/999"):
            return httpx.Response(
                200,
                json={
                    "state": "APPROVED",
                    "commit_id": COMMIT_A,
                    "submitted_at": "2026-09-18T10:00:00Z",
                    "user": {"login": "reviewer_alice"},
                },
            )
        if url.endswith(f"/repos/AI-HealthCare-05/AH_05_04/compare/develop...{COMMIT_A}"):
            return httpx.Response(200, json={"status": "behind", "ahead_by": 0})
        if f"/repos/AI-HealthCare-05/AH_05_04/contents/{c1_approval_artifact_path(772, 999)}?ref={COMMIT_A}" in url:
            return httpx.Response(200, json={"content": blob_b64, "encoding": "base64"})
        return httpx.Response(404)

    source = GitHubTrustedApprovalSource(
        repository=REPO,
        default_branch=DEFAULT_BRANCH,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProtectedSecurityError) as exc_info:
        await source.fetch(source_event_id)
    assert exc_info.value.reason_code == "INTERNAL_ERROR"
