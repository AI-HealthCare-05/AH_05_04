from __future__ import annotations

import base64
import contextlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr

from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    ApprovalSourceEvidence,
    ProtectedApprovalPrincipal,
    ProtectedSecurityError,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import (
    ApprovalSourceNotFoundError,
    C1ApprovalArtifact,
    FreezeApprovalArtifact,
    FreezeApprovalLocator,
    FreezeApprovalSourceEvidence,
    c1_approval_artifact_path,
    compute_approval_canonical_raw_sha256,
    freeze_approval_artifact_path,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

_C1_SOURCE_PATTERN = re.compile(
    r"^github:(?P<owner>[a-zA-Z0-9_.-]+)/(?P<repo>[a-zA-Z0-9_.-]+):pull:(?P<pull_number>[1-9][0-9]*):review:(?P<review_id>[1-9][0-9]*)$"
)


@dataclass(frozen=True)
class _ApprovedReviewMetadata:
    commit_id: str
    user_login: str
    submitted_at_dt: datetime


def _parse_iso8601_utc(raw_submitted_at: str) -> datetime:
    try:
        return datetime.fromisoformat(raw_submitted_at.replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        raise ProtectedSecurityError("INTERNAL_ERROR") from None


class GitHubTrustedApprovalSource:
    """Production TrustedApprovalSource implementation backed by GitHub REST API."""

    def __init__(
        self,
        *,
        repository: str,
        default_branch: str,
        token: str | SecretStr,
        transport: httpx.AsyncBaseTransport | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        if not repository or "/" not in repository:
            raise ValueError("repository must be in '<owner>/<repo>' format")
        parts = repository.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("repository must be in '<owner>/<repo>' format")

        if not default_branch or not default_branch.strip():
            raise ValueError("default_branch must be non-empty")

        raw_token = token.get_secret_value() if isinstance(token, SecretStr) else token
        if not raw_token or not raw_token.strip():
            raise ValueError("token must be non-empty")

        self._repository = repository
        self._owner, self._repo = parts
        self._default_branch = default_branch.strip()
        self._token = raw_token.strip()
        self._transport = transport
        self._client_factory = client_factory
        self._base_url = base_url.rstrip("/")

    @contextlib.asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client_factory is not None:
            client = self._client_factory()
            try:
                yield client
            finally:
                await client.aclose()
            return

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AH0504-ProtectedRetrieval/1.0",
        }
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            transport=self._transport,
            timeout=10.0,
        ) as client:
            yield client

    async def _probe_repo_accessible(self, client: httpx.AsyncClient) -> bool:
        """Probe repository accessibility without leaking details. Returns True if repo exists and token is valid."""
        try:
            response = await client.get(f"/repos/{self._owner}/{self._repo}")
            return response.status_code == 200
        except Exception:
            return False

    async def _probe_pr_capability(self, client: httpx.AsyncClient) -> bool:
        """Probe Pull Request read capability on repository."""
        try:
            response = await client.get(
                f"/repos/{self._owner}/{self._repo}/pulls",
                params={"per_page": 1},
            )
            return response.status_code == 200
        except Exception:
            return False

    async def _probe_commit_accessible(self, client: httpx.AsyncClient, commit_id: str) -> bool:
        """Probe whether exact commit/ref is accessible."""
        try:
            response = await client.get(f"/repos/{self._owner}/{self._repo}/commits/{commit_id}")
            return response.status_code == 200
        except Exception:
            return False

    async def _probe_contents_capability(self, client: httpx.AsyncClient, commit_id: str) -> bool:
        """Probe repository contents capability at the given commit."""
        try:
            response = await client.get(
                f"/repos/{self._owner}/{self._repo}/contents",
                params={"ref": commit_id},
            )
            return response.status_code == 200
        except Exception:
            return False

    async def _get_pr_or_classify_404(
        self,
        client: httpx.AsyncClient,
        pull_number: int,
    ) -> dict[str, Any]:
        try:
            response = await client.get(f"/repos/{self._owner}/{self._repo}/pulls/{pull_number}")
        except httpx.RequestError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None

        if response.status_code == 200:
            try:
                return response.json()
            except Exception:
                raise ProtectedSecurityError("INTERNAL_ERROR") from None

        if response.status_code == 404:
            repo_ok = await self._probe_repo_accessible(client)
            pr_ok = await self._probe_pr_capability(client)
            if repo_ok and pr_ok:
                raise ApprovalSourceNotFoundError(f"Pull request {pull_number} not found")
            raise ProtectedSecurityError("INTERNAL_ERROR")

        raise ProtectedSecurityError("INTERNAL_ERROR")

    async def _get_review_or_classify_404(
        self,
        client: httpx.AsyncClient,
        pull_number: int,
        review_id: int,
    ) -> dict[str, Any]:
        try:
            response = await client.get(f"/repos/{self._owner}/{self._repo}/pulls/{pull_number}/reviews/{review_id}")
        except httpx.RequestError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None

        if response.status_code == 200:
            try:
                return response.json()
            except Exception:
                raise ProtectedSecurityError("INTERNAL_ERROR") from None

        if response.status_code == 404:
            try:
                pr_resp = await client.get(f"/repos/{self._owner}/{self._repo}/pulls/{pull_number}")
                pr_accessible = pr_resp.status_code == 200
            except Exception:
                pr_accessible = False

            if pr_accessible:
                raise ApprovalSourceNotFoundError(f"Review {review_id} not found on PR {pull_number}")
            raise ProtectedSecurityError("INTERNAL_ERROR")

        raise ProtectedSecurityError("INTERNAL_ERROR")

    async def _verify_ancestry(self, client: httpx.AsyncClient, commit_id: str) -> None:
        endpoint = f"/repos/{self._owner}/{self._repo}/compare/{self._default_branch}...{commit_id}"
        try:
            response = await client.get(endpoint)
        except httpx.RequestError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None

        if response.status_code != 200:
            raise ProtectedSecurityError("INTERNAL_ERROR")

        try:
            compare_data = response.json()
        except Exception:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None

        status = compare_data.get("status")
        ahead_by = compare_data.get("ahead_by", 0)
        # Commit must be in default branch history (status "behind" or "identical", ahead_by == 0)
        if status not in ("behind", "identical") or ahead_by != 0:
            raise ProtectedSecurityError("INTERNAL_ERROR")

    async def _verify_pr_and_approved_review(
        self,
        client: httpx.AsyncClient,
        pull_number: int,
        review_id: int,
    ) -> _ApprovedReviewMetadata:
        # 1. Verify PR exists and base repository matches authority (reject forks)
        pr_data = await self._get_pr_or_classify_404(client, pull_number)
        base_full_name = pr_data.get("base", {}).get("repo", {}).get("full_name")
        head_full_name = pr_data.get("head", {}).get("repo", {}).get("full_name")
        if base_full_name != self._repository or head_full_name != self._repository:
            raise ProtectedSecurityError("INTERNAL_ERROR")

        # 2. Verify review exists and is APPROVED
        review_data = await self._get_review_or_classify_404(client, pull_number, review_id)
        state = review_data.get("state")
        if state != "APPROVED":
            raise ApprovalSourceNotFoundError(f"Review {review_id} is not APPROVED (state={state})")

        commit_id = review_data.get("commit_id")
        if not commit_id or not re.match(r"^[0-9a-f]{40}$", commit_id):
            raise ProtectedSecurityError("INTERNAL_ERROR")

        user_login = review_data.get("user", {}).get("login")
        if not user_login:
            raise ProtectedSecurityError("INTERNAL_ERROR")

        raw_submitted_at = review_data.get("submitted_at")
        if not raw_submitted_at:
            raise ProtectedSecurityError("INTERNAL_ERROR")
        submitted_at_dt = _parse_iso8601_utc(raw_submitted_at)

        # 3. Verify commit is merged into protected branch
        await self._verify_ancestry(client, commit_id)

        return _ApprovedReviewMetadata(
            commit_id=commit_id,
            user_login=user_login,
            submitted_at_dt=submitted_at_dt,
        )

    async def _fetch_blob(
        self,
        client: httpx.AsyncClient,
        path: str,
        commit_id: str,
    ) -> bytes:
        try:
            response = await client.get(
                f"/repos/{self._owner}/{self._repo}/contents/{path}",
                params={"ref": commit_id},
            )
        except httpx.RequestError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None

        if response.status_code == 200:
            try:
                content_data = response.json()
            except Exception:
                raise ProtectedSecurityError("INTERNAL_ERROR") from None

            raw_content = content_data.get("content")
            encoding = content_data.get("encoding")
            if not raw_content or encoding != "base64":
                raise ProtectedSecurityError("INTERNAL_ERROR")

            try:
                return base64.b64decode(raw_content)
            except Exception:
                raise ProtectedSecurityError("INTERNAL_ERROR") from None

        if response.status_code == 404:
            commit_ok = await self._probe_commit_accessible(client, commit_id)
            contents_ok = await self._probe_contents_capability(client, commit_id)
            if commit_ok and contents_ok:
                raise ApprovalSourceNotFoundError(f"Artifact {path} not found at commit {commit_id}")
            raise ProtectedSecurityError("INTERNAL_ERROR")

        raise ProtectedSecurityError("INTERNAL_ERROR")

    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence:
        match = _C1_SOURCE_PATTERN.match(source_event_id)
        if not match:
            async with self._get_client() as client:
                repo_ok = await self._probe_repo_accessible(client)
                if repo_ok:
                    raise ApprovalSourceNotFoundError(f"Malformed or unsupported C1 source event ID: {source_event_id}")
                raise ProtectedSecurityError("INTERNAL_ERROR")

        owner = match.group("owner")
        repo = match.group("repo")
        pull_number = int(match.group("pull_number"))
        review_id = int(match.group("review_id"))

        if f"{owner}/{repo}" != self._repository:
            async with self._get_client() as client:
                repo_ok = await self._probe_repo_accessible(client)
                if repo_ok:
                    raise ApprovalSourceNotFoundError(f"Approval source repository mismatch: {owner}/{repo}")
                raise ProtectedSecurityError("INTERNAL_ERROR")

        async with self._get_client() as client:
            review = await self._verify_pr_and_approved_review(client, pull_number, review_id)
            exact_path = c1_approval_artifact_path(pull_number, review_id)
            blob_bytes = await self._fetch_blob(client, exact_path, review.commit_id)
            approval_artifact_sha256 = sha256(blob_bytes).hexdigest()

            try:
                artifact = C1ApprovalArtifact.model_validate_json(blob_bytes)
            except Exception:
                raise ProtectedSecurityError("INTERNAL_ERROR") from None

            if artifact.source_event_id != source_event_id or artifact.target_commit_oid != review.commit_id:
                raise ProtectedSecurityError("INTERNAL_ERROR")

            canonical_raw_sha256 = compute_approval_canonical_raw_sha256(
                review_id=review_id,
                state="APPROVED",
                submitted_at=review.submitted_at_dt,
                commit_id=review.commit_id,
                reviewer_actor_id=review.user_login,
                repository=self._repository,
                pull_number=pull_number,
                approval_artifact_sha256=approval_artifact_sha256,
            )

            return ApprovalSourceEvidence(
                source_event_id=source_event_id,
                authorization_action=artifact.authorization_action,
                approved_grant_payload_sha256=artifact.approved_grant_payload_sha256,
                issuer=ProtectedApprovalPrincipal(
                    actor=ActorIdentity(namespace="GITHUB_LOGIN", actor_id=review.user_login),
                    role=artifact.issuer_role,
                ),
                state="APPROVED",
                recorded_at=review.submitted_at_dt,
                target_commit_oid=review.commit_id,
                target_artifact_sha256=artifact.target_artifact_sha256,
                canonical_raw_sha256=canonical_raw_sha256,
                implementation_participants=artifact.implementation_participants,
            )

    async def fetch_freeze(
        self,
        source_event_id: str,
        locator: FreezeApprovalLocator | None = None,
    ) -> FreezeApprovalSourceEvidence:
        if locator is None or locator.source_event_id != source_event_id:
            async with self._get_client() as client:
                repo_ok = await self._probe_repo_accessible(client)
                if repo_ok:
                    raise ApprovalSourceNotFoundError(
                        f"Freeze approval locator mismatch or missing for {source_event_id}"
                    )
                raise ProtectedSecurityError("INTERNAL_ERROR")

        pull_number = locator.pull_number
        review_id = locator.review_id

        async with self._get_client() as client:
            review = await self._verify_pr_and_approved_review(client, pull_number, review_id)
            exact_path = freeze_approval_artifact_path(source_event_id)
            blob_bytes = await self._fetch_blob(client, exact_path, review.commit_id)
            approval_artifact_sha256 = sha256(blob_bytes).hexdigest()

            try:
                artifact = FreezeApprovalArtifact.model_validate_json(blob_bytes)
            except Exception:
                raise ProtectedSecurityError("INTERNAL_ERROR") from None

            if artifact.source_event_id != source_event_id or artifact.target_commit_oid != review.commit_id:
                raise ProtectedSecurityError("INTERNAL_ERROR")

            canonical_raw_sha256 = compute_approval_canonical_raw_sha256(
                review_id=review_id,
                state="APPROVED",
                submitted_at=review.submitted_at_dt,
                commit_id=review.commit_id,
                reviewer_actor_id=review.user_login,
                repository=self._repository,
                pull_number=pull_number,
                approval_artifact_sha256=approval_artifact_sha256,
            )

            return FreezeApprovalSourceEvidence(
                source_event_id=source_event_id,
                action=artifact.action,
                dataset_id=artifact.dataset_id,
                dataset_version=artifact.dataset_version,
                manifest_sha256=artifact.manifest_sha256,
                protected_artifact_sha256=artifact.protected_artifact_sha256,
                authored_count=artifact.authored_count,
                review_complete=artifact.review_complete,
                leakage_axis_intersections=artifact.leakage_axis_intersections,
                issuer=ProtectedApprovalPrincipal(
                    actor=ActorIdentity(namespace="GITHUB_LOGIN", actor_id=review.user_login),
                    role=artifact.issuer_role,
                ),
                state="APPROVED",
                recorded_at=review.submitted_at_dt,
                target_commit_oid=review.commit_id,
                target_artifact_sha256=artifact.target_artifact_sha256,
                canonical_raw_sha256=canonical_raw_sha256,
                implementation_participants=artifact.implementation_participants,
            )
