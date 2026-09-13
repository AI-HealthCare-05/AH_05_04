import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError

from app.core import config
from app.core.utils.idempotency import (
    compute_key_hmac,
    compute_request_hash,
    validate_idempotency_key_format,
)
from app.models.async_jobs import IdempotencyRecord
from app.repositories.idempotency_repository import (
    IdempotencyRepository,
    is_sync_idempotency_scope_conflict,
)

# idempotency-v1.md: snapshot 물리 저장은 BYTEA, application cap은 1MiB.
SNAPSHOT_SIZE_CAP_BYTES = 1024 * 1024

# medication-identification-v1.md #identification-preflight가 정의하는 Runtime Release
# Bundle은 #175(RAG-12A)가 아직 구현되지 않아 실제 버전 식별자가 없다. request hash가
# 요구하는 자리를 생략하면 #175가 착지했을 때 fingerprint shape이 바뀌어 그 이전에 저장된
# 요청 지문과 호환성이 깨진다 — Option B로 이 자리를 명시적 placeholder로 채워 두고, #175
# 병합 시 이 상수 사용처를 실제 버전 식별자로 교체한다.
RUNTIME_RELEASE_BUNDLE_PLACEHOLDER = "runtime-release-bundle-not-implemented-issue-175"


class IdempotencyKeyConflictError(Exception):
    """같은 `Idempotency-Key`로 다른 요청 지문이 접수됐을 때 발생합니다(409 IDEMPOTENCY_KEY_CONFLICT).
    `job_intake.py`의 동명 클래스와 동일한 역할을 SYNC_MUTATION scope에 적용합니다."""


class IdempotencyResponseTooLargeError(Exception):
    """암호화된 snapshot이 1MiB cap을 초과할 때 발생합니다(503 IDEMPOTENCY_RESPONSE_TOO_LARGE).
    이 예외는 `mutate` 콜백과 같은 `session.begin_nested()` 블록 안에서 발생시켜, 호출자가
    별도로 domain mutation을 롤백하지 않아도 되도록 원자적으로 함께 취소합니다."""


class SnapshotCipher(Protocol):
    """`response_body_snapshot` 암호화 envelope 인터페이스입니다.

    실제 알고리즘과 키 관리 방식은 이 파일에서 정하지 않습니다 — #311 이슈가 명시한 대로
    담당 리뷰어(권가빈)의 암호화 envelope 검토와 Privacy·Security 리뷰를 먼저 거쳐야 하는
    영역이라, `SyncMutationIdempotencyService`는 이 프로토콜에만 의존하고 구체 구현은
    DI 계층에서 그 결정이 난 뒤에 연결합니다.
    """

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        """반환값은 `(ciphertext, encryption_key_version)`입니다."""
        ...

    def decrypt(self, ciphertext: bytes, *, key_version: str) -> bytes: ...


class FernetSnapshotCipher:
    """`SnapshotCipher`의 기본 구현입니다. `cryptography`의 Fernet(AES-128-CBC + HMAC-SHA256
    인증 암호화)로 `Config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY`를 사용합니다.

    이 구현 자체가 암호화 envelope의 최종 결정은 아닙니다 — "연결까지는 해두고 실제 값은
    나중에 입력"할 수 있도록 알고리즘과 배선을 미리 갖춰 두는 기본값이며, 담당 리뷰어의
    envelope·키 관리 검토 결과에 따라 `SnapshotCipher`를 구현하는 다른 클래스로 교체될 수
    있습니다. 그때까지는 `Config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY`를 실제 운영 secret
    으로 확정하지 않습니다.
    """

    def __init__(
        self,
        *,
        key: str,
        key_version: str,
        retired_keys: dict[str, str] | None = None,
    ) -> None:
        """`retired_keys`(PR #346 리뷰)는 `encryption_key_version -> key` 매핑으로, key/version을
        교체한 뒤에도 아직 만료(TTL)되지 않은 기존 레코드를 복호화하기 위한 decrypt 전용 key
        ring이다. 새 쓰기는 항상 `key`/`key_version`(active)만 사용한다.

        같은 version 문자열이 active와 retired 양쪽에 다른 의미로 배포되는 구성은 어느 키로
        복호화해야 할지 알 수 없는 모호한 상태라, 기동 시 바로 막는다(운영 절차로 강제하지
        않고 코드로 차단)."""
        if retired_keys and key_version in retired_keys:
            raise ValueError(
                f"encryption_key_version {key_version!r}는 active key에도 retired_keys에도 "
                "쓰일 수 없습니다 — 같은 version 문자열에 다른 key가 배포되는 구성을 막습니다."
            )
        self._active_key_version = key_version
        self._fernets: dict[str, Fernet] = {key_version: Fernet(key.encode("utf-8"))}
        for version, retired_key in (retired_keys or {}).items():
            self._fernets[version] = Fernet(retired_key.encode("utf-8"))

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        return self._fernets[self._active_key_version].encrypt(plaintext), self._active_key_version

    def decrypt(self, ciphertext: bytes, *, key_version: str) -> bytes:
        fernet = self._fernets.get(key_version)
        if fernet is None:
            raise InvalidToken(f"Unsupported encryption_key_version: {key_version}")
        return fernet.decrypt(ciphertext)


def get_default_snapshot_cipher() -> FernetSnapshotCipher:
    return FernetSnapshotCipher(
        key=config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY,
        key_version=config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY_VERSION,
        retired_keys=config.IDEMPOTENCY_SNAPSHOT_ENCRYPTION_RETIRED_KEYS,
    )


@dataclass
class SyncMutationResult:
    response_status: int
    response_body: dict[str, Any]
    # True면 mutate()를 다시 실행하지 않고 저장된 snapshot을 그대로 재현한 것입니다.
    is_replay: bool


Mutate = Callable[[], Awaitable[dict[str, Any]]]


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """`compute_request_hash`와 동일한 canonical 직렬화 규칙(정렬된 key, 구분자 압축)을
    snapshot 저장에도 적용해, 같은 응답이 항상 같은 바이트로 직렬화되게 합니다."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class SyncMutationIdempotencyService:
    """Track B·C·F 동기 상태 변경 API가 공통으로 재사용하는 `record_type=SYNC_MUTATION`
    멱등성 처리입니다(idempotency-v1.md "동기 상태 변경 처리 규칙").

    호출자는 실제 도메인 mutation을 `mutate` 콜백으로 제공합니다. 이 콜백이 예외 없이
    반환하면 성공(2xx)으로 간주해 snapshot을 저장하고, 도메인 오류(`ApiError` 등)를 그대로
    raise하면 이 service를 그대로 통과해 전파되며 아무것도 저장하지 않습니다(계약: "2xx만
    저장"). `mutate` 호출과 snapshot 저장은 `session.begin_nested()`로 같은 원자 단위에
    묶여서, snapshot이 cap을 초과해 실패하면 도메인 mutation도 함께 롤백됩니다.

    `JobIntakeService.accept_job`(ASYNC_JOB)과 동일한 구조(재현/만료 삭제/동시 경쟁 처리)를
    SYNC_MUTATION scope(`parent_resource_id` 포함)로 반복합니다.
    """

    def __init__(self, repository: IdempotencyRepository, cipher: SnapshotCipher) -> None:
        self._repository = repository
        self._cipher = cipher

    async def execute(
        self,
        *,
        user_id: UUID,
        operation_id: str,
        parent_resource_id: UUID,
        idempotency_key: str,
        fingerprint: dict[str, Any],
        success_status: int,
        mutate: Mutate,
    ) -> SyncMutationResult:
        validate_idempotency_key_format(idempotency_key)

        key_hmac = compute_key_hmac(idempotency_key, hmac_key=config.IDEMPOTENCY_HMAC_KEY)
        request_hash = compute_request_hash(fingerprint)

        existing = await self._repository.find_sync_idempotency_record(
            user_id=user_id,
            operation_id=operation_id,
            parent_resource_id=parent_resource_id,
            key_hmac=key_hmac,
        )
        if existing is not None:
            if existing.expires_at > datetime.now(config.TIMEZONE):
                return self._resolve_existing_record(existing, request_hash=request_hash)
            # 삭제 이후 다른 요청이 먼저 새 레코드를 만드는 경쟁은 아래 except IntegrityError
            # 블록의 재조회 경로로 흡수됩니다(AsyncJobRepository/JobIntakeService와 동일 패턴).
            await self._repository.delete_expired_idempotency_record(record_id=existing.id)

        session = self._repository.session
        try:
            async with session.begin_nested():
                response_body = await mutate()
                plaintext = _canonical_json_bytes(response_body)
                ciphertext, encryption_key_version = self._cipher.encrypt(plaintext)
                if len(ciphertext) > SNAPSHOT_SIZE_CAP_BYTES:
                    raise IdempotencyResponseTooLargeError("IDEMPOTENCY_RESPONSE_TOO_LARGE")
                await self._repository.create_sync_idempotency_record(
                    user_id=user_id,
                    operation_id=operation_id,
                    parent_resource_id=parent_resource_id,
                    key_hmac=key_hmac,
                    request_hash=request_hash,
                    response_status=success_status,
                    response_body_snapshot=ciphertext,
                    encryption_key_version=encryption_key_version,
                )
        except IntegrityError as exc:
            if not is_sync_idempotency_scope_conflict(exc):
                raise
            # 동시 최초 요청 경쟁 — 패자는 승자가 저장한 레코드를 다시 조회해 지문을 비교합니다
            # (idempotency-v1.md: "동시 최초 요청은 DB unique constraint로 하나만 승리시킨 뒤,
            # 패자는 저장된 요청 지문을 비교해 규칙을 적용한다"). 위 begin_nested()가 이미
            # 패자의 mutate() 부작용을 롤백했습니다.
            existing = await self._repository.find_sync_idempotency_record(
                user_id=user_id,
                operation_id=operation_id,
                parent_resource_id=parent_resource_id,
                key_hmac=key_hmac,
            )
            if existing is None:
                raise
            return self._resolve_existing_record(existing, request_hash=request_hash)
        except Exception:
            # mutate()가 IntegrityError가 아닌 도메인 예외로 실패했을 수 있다(PR #346 리뷰).
            # 예: 동일 key의 두 요청이 동시에 여기 들어와 각자 mutate()를 실행하면, 그 안에서
            # 실제 domain lock(FOR UPDATE 등)으로 순서가 정해지고 패자는 승자가 커밋한 상태를
            # 보고 도메인 충돌(예: ApiError 409)을 던진다 — 이 경로는 위 IntegrityError 분기에
            # 도달하지 않으므로, 그대로 두면 같은 key·같은 지문의 재현 요청이 도메인 오류로
            # 끝나 계약("동일 key·동일 요청은 최초 200 snapshot을 재현")을 어긴다. 승자가 이미
            # 레코드를 저장했다면 그걸 다시 조회해 지문이 같으면 재현하고, 없으면(진짜 무관한
            # 오류라면) 원래 예외를 그대로 전파한다.
            existing = await self._repository.find_sync_idempotency_record(
                user_id=user_id,
                operation_id=operation_id,
                parent_resource_id=parent_resource_id,
                key_hmac=key_hmac,
            )
            if existing is None:
                raise
            return self._resolve_existing_record(existing, request_hash=request_hash)

        return SyncMutationResult(response_status=success_status, response_body=response_body, is_replay=False)

    def _resolve_existing_record(
        self,
        record: IdempotencyRecord,
        *,
        request_hash: str,
    ) -> SyncMutationResult:
        if record.request_hash != request_hash:
            raise IdempotencyKeyConflictError("IDEMPOTENCY_KEY_CONFLICT")

        if (
            record.response_status is None
            or record.response_body_snapshot is None
            or record.encryption_key_version is None
        ):
            raise RuntimeError("SYNC_MUTATION idempotency record must carry a stored response snapshot")

        plaintext = self._cipher.decrypt(record.response_body_snapshot, key_version=record.encryption_key_version)
        response_body: dict[str, Any] = json.loads(plaintext.decode("utf-8"))
        return SyncMutationResult(response_status=record.response_status, response_body=response_body, is_replay=True)
