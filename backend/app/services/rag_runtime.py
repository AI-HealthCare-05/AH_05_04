from app.models.rag_runtime import RagRuntimeEnvironmentTransition, RagRuntimeEnvironmentTransitionKind
from app.repositories.rag_runtime_repository import (
    RagRuntimeEnvironmentTransitionCreate,
    RagRuntimeRepository,
    RuntimeEnvironmentTransitionInvalidError,
)


class RagRuntimeEnvironmentTransitionService:
    """Authorized Runtime pointer transition boundary.

    Authentication and Guard evaluation happen before this internal service is
    called. The guard decision reference and authenticated actor are mandatory;
    the Repository rechecks all mutable database state under the environment lock.
    """

    def __init__(self, repository: RagRuntimeRepository) -> None:
        self.repository = repository

    async def transition(
        self,
        command: RagRuntimeEnvironmentTransitionCreate,
    ) -> RagRuntimeEnvironmentTransition:
        self._validate_command(command)
        return await self.repository.transition_environment(command)

    @staticmethod
    def _validate_command(command: RagRuntimeEnvironmentTransitionCreate) -> None:
        if command.transition_kind not in {
            RagRuntimeEnvironmentTransitionKind.PLANNED_ACTIVATION,
            RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK,
            RagRuntimeEnvironmentTransitionKind.SUSPEND,
            RagRuntimeEnvironmentTransitionKind.RESUME,
        }:
            raise RuntimeEnvironmentTransitionInvalidError("Unsupported Runtime transition")
        if command.expected_environment_revision < 1 or command.expected_safety_epoch < 1:
            raise RuntimeEnvironmentTransitionInvalidError("Expected Runtime revision is invalid")
        if not command.guard_decision_ref.strip() or command.created_by is None or not command.created_by.strip():
            raise RuntimeEnvironmentTransitionInvalidError("Guard decision and authenticated actor are required")
        if command.transition_kind in {
            RagRuntimeEnvironmentTransitionKind.EMERGENCY_ROLLBACK,
            RagRuntimeEnvironmentTransitionKind.SUSPEND,
        } and (command.transition_reason_code is None or not command.transition_reason_code.strip()):
            raise RuntimeEnvironmentTransitionInvalidError("Safety transition reason is required")
        expected_pair = (
            command.expected_active_bundle_id is None,
            command.expected_active_bundle_manifest_hash is None,
        )
        target_pair = (command.target_bundle_id is None, command.target_bundle_manifest_hash is None)
        if expected_pair[0] != expected_pair[1] or target_pair[0] != target_pair[1]:
            raise RuntimeEnvironmentTransitionInvalidError(
                "Runtime bundle id and manifest hash must be supplied together"
            )
