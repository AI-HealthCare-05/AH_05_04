from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.utils.idempotency import IdempotencyKeyFormatError, compute_key_hmac
from app.models.async_jobs import IdempotencyRecord
from app.models.users import Gender, User
from app.repositories.idempotency_repository import IdempotencyRepository
from app.services.idempotency import (
    FernetSnapshotCipher,
    IdempotencyKeyConflictError,
    IdempotencyResponseTooLargeError,
    SyncMutationIdempotencyService,
    get_default_snapshot_cipher,
)
from app.tests.conftest import test_engine

OPERATION_ID = "medication-candidate.confirm"
REAL_FERNET_KEY = "mNZgOOlYI_KL5_6HjgyDFGPkMW7xU7CBpPYY5awEaRg="


class _FakeCipher:
    """테스트 전용 reversible cipher입니다 — 실제 암호화가 아니며 프로덕션 코드에는
    쓰지 않습니다. 암호화 envelope의 실제 알고리즘·키 관리는 아직 결정되지 않았습니다."""

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        return plaintext[::-1], "test-v1"

    def decrypt(self, ciphertext: bytes, *, key_version: str) -> bytes:
        assert key_version == "test-v1"
        return ciphertext[::-1]


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # 다른 service 테스트와 동일한 savepoint 격리 방식입니다.
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


async def _create_user(session: AsyncSession) -> User:
    user = User(
        email=f"idem-svc-{uuid4().hex[:12]}@example.com",
        hashed_password="hashed-password",
        name="테스트 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    return user


def _service(session: AsyncSession) -> SyncMutationIdempotencyService:
    return SyncMutationIdempotencyService(IdempotencyRepository(session), _FakeCipher())


async def _count_idempotency_records(session: AsyncSession) -> int:
    result = await session.execute(select(IdempotencyRecord))
    return len(result.scalars().all())


async def test_execute_rejects_malformed_idempotency_key_before_running_mutation(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    calls = 0

    async def mutate() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    with pytest.raises(IdempotencyKeyFormatError):
        await _service(db_session).execute(
            user_id=user.id,
            operation_id=OPERATION_ID,
            parent_resource_id=uuid4(),
            idempotency_key="too-short",
            fingerprint={"action": "confirm"},
            success_status=200,
            mutate=mutate,
        )

    assert calls == 0


async def test_execute_runs_mutation_once_and_stores_snapshot_on_first_call(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    parent_resource_id = uuid4()
    calls = 0

    async def mutate() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"identification_id": "abc", "status": "MATCHED"}

    result = await _service(db_session).execute(
        user_id=user.id,
        operation_id=OPERATION_ID,
        parent_resource_id=parent_resource_id,
        idempotency_key="a" * 32,
        fingerprint={"action": "confirm", "candidate_search_result_id": "r1"},
        success_status=200,
        mutate=mutate,
    )

    assert calls == 1
    assert result.is_replay is False
    assert result.response_status == 200
    assert result.response_body == {"identification_id": "abc", "status": "MATCHED"}
    assert await _count_idempotency_records(db_session) == 1


async def test_execute_replays_stored_snapshot_without_rerunning_mutation(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    parent_resource_id = uuid4()
    calls = 0

    async def mutate() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"identification_id": "abc", "status": "MATCHED"}

    service = _service(db_session)
    fingerprint = {"action": "confirm", "candidate_search_result_id": "r1"}
    first = await service.execute(
        user_id=user.id,
        operation_id=OPERATION_ID,
        parent_resource_id=parent_resource_id,
        idempotency_key="a" * 32,
        fingerprint=fingerprint,
        success_status=200,
        mutate=mutate,
    )
    second = await service.execute(
        user_id=user.id,
        operation_id=OPERATION_ID,
        parent_resource_id=parent_resource_id,
        idempotency_key="a" * 32,
        fingerprint=fingerprint,
        success_status=200,
        mutate=mutate,
    )

    assert calls == 1
    assert second.is_replay is True
    assert second.response_body == first.response_body
    assert await _count_idempotency_records(db_session) == 1


async def test_execute_raises_conflict_for_different_fingerprint_without_side_effects(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    parent_resource_id = uuid4()
    calls = 0

    async def mutate() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"identification_id": "abc", "status": "MATCHED"}

    service = _service(db_session)
    await service.execute(
        user_id=user.id,
        operation_id=OPERATION_ID,
        parent_resource_id=parent_resource_id,
        idempotency_key="a" * 32,
        fingerprint={"action": "confirm", "candidate_search_result_id": "r1"},
        success_status=200,
        mutate=mutate,
    )

    with pytest.raises(IdempotencyKeyConflictError):
        await service.execute(
            user_id=user.id,
            operation_id=OPERATION_ID,
            parent_resource_id=parent_resource_id,
            idempotency_key="a" * 32,
            fingerprint={"action": "confirm", "candidate_search_result_id": "different-result"},
            success_status=200,
            mutate=mutate,
        )

    # 지문이 다르면 409로 끝나야 하고, 저장된 최초 요청을 다시 실행하면 안 된다.
    assert calls == 1
    assert await _count_idempotency_records(db_session) == 1


async def test_execute_does_not_persist_record_when_mutation_raises(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)

    class _DomainError(Exception):
        pass

    async def mutate() -> dict[str, object]:
        raise _DomainError("CANDIDATE_SEARCH_NOT_FOUND")

    with pytest.raises(_DomainError):
        await _service(db_session).execute(
            user_id=user.id,
            operation_id=OPERATION_ID,
            parent_resource_id=uuid4(),
            idempotency_key="a" * 32,
            fingerprint={"action": "confirm"},
            success_status=200,
            mutate=mutate,
        )

    assert await _count_idempotency_records(db_session) == 0


async def test_execute_rolls_back_mutation_side_effects_when_mutation_raises(
    db_session: AsyncSession,
) -> None:
    """mutate()가 domain 변경을 실제로 쓴 뒤 실패하면, session.begin_nested()가 그 변경도
    함께 롤백해야 한다 — 4xx/5xx 경로에서 idempotency record뿐 아니라 부분 실행된 domain
    부작용도 남으면 안 된다."""
    user = await _create_user(db_session)
    side_effect_email = f"side-effect-{uuid4().hex[:8]}@example.com"

    class _DomainError(Exception):
        pass

    async def mutate() -> dict[str, object]:
        await _create_user_with_email(db_session, side_effect_email)
        raise _DomainError("boom")

    with pytest.raises(_DomainError):
        await _service(db_session).execute(
            user_id=user.id,
            operation_id=OPERATION_ID,
            parent_resource_id=uuid4(),
            idempotency_key="a" * 32,
            fingerprint={"action": "confirm"},
            success_status=200,
            mutate=mutate,
        )

    result = await db_session.execute(select(User).where(User.email == side_effect_email))
    assert result.scalar_one_or_none() is None


async def test_execute_raises_response_too_large_and_rolls_back_domain_side_effects(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    side_effect_email = f"side-effect-{uuid4().hex[:8]}@example.com"
    oversized_payload = "x" * (1024 * 1024 + 1)

    async def mutate() -> dict[str, object]:
        await _create_user_with_email(db_session, side_effect_email)
        return {"payload": oversized_payload}

    with pytest.raises(IdempotencyResponseTooLargeError):
        await _service(db_session).execute(
            user_id=user.id,
            operation_id=OPERATION_ID,
            parent_resource_id=uuid4(),
            idempotency_key="a" * 32,
            fingerprint={"action": "confirm"},
            success_status=200,
            mutate=mutate,
        )

    assert await _count_idempotency_records(db_session) == 0
    result = await db_session.execute(select(User).where(User.email == side_effect_email))
    assert result.scalar_one_or_none() is None


async def test_execute_reclaims_expired_record_and_reruns_mutation(
    db_session: AsyncSession,
) -> None:
    """같은 Idempotency-Key라도 저장된 레코드가 이미 만료됐다면(expires_at 경과) 재현하지
    않고 mutate()를 다시 실행해야 한다 — 계약: "만료 이후 같은 key는 새 요청으로 처리될 수
    있다". 만료 레코드는 삭제 후 새 레코드로 교체되어야 하므로 최종 개수는 1건이어야 한다."""
    user = await _create_user(db_session)
    parent_resource_id = uuid4()
    idempotency_key = "b" * 32
    key_hmac = compute_key_hmac(idempotency_key, hmac_key=config.IDEMPOTENCY_HMAC_KEY)

    repository = IdempotencyRepository(db_session)
    stale_record = await repository.create_sync_idempotency_record(
        user_id=user.id,
        operation_id=OPERATION_ID,
        parent_resource_id=parent_resource_id,
        key_hmac=key_hmac,
        request_hash="stale-request-hash",
        response_status=200,
        response_body_snapshot=b"stale",
        encryption_key_version="test-v1",
    )
    stale_record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    calls = 0

    async def mutate() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"identification_id": "fresh"}

    result = await _service(db_session).execute(
        user_id=user.id,
        operation_id=OPERATION_ID,
        parent_resource_id=parent_resource_id,
        idempotency_key=idempotency_key,
        fingerprint={"action": "confirm"},
        success_status=200,
        mutate=mutate,
    )

    assert calls == 1
    assert result.is_replay is False
    assert result.response_body == {"identification_id": "fresh"}
    assert await _count_idempotency_records(db_session) == 1


async def test_execute_resolves_concurrent_insert_race_by_replaying_winner(
    db_session: AsyncSession,
) -> None:
    """동시 최초 요청 경쟁: find_sync_idempotency_record가 아직 아무것도 없다고 본 뒤에
    다른 요청(승자)이 먼저 record를 만들면, insert 시점에 unique index 위반이 난다. 패자는
    자신의 mutate() 부작용을 롤백하고 승자의 저장된 응답을 재현해야 한다."""
    user = await _create_user(db_session)
    parent_resource_id = uuid4()
    idempotency_key = "c" * 32
    fingerprint = {"action": "confirm"}
    # service가 내부적으로 만드는 별도 repository가 아니라, 아래에서 monkeypatch할 이
    # repository 인스턴스를 service에 직접 주입해야 patch가 실제로 적용된다.
    repository = IdempotencyRepository(db_session)
    service = SyncMutationIdempotencyService(repository, _FakeCipher())

    winner_calls = 0

    async def winner_mutate() -> dict[str, object]:
        nonlocal winner_calls
        winner_calls += 1
        return {"identification_id": "winner"}

    winner_result = await service.execute(
        user_id=user.id,
        operation_id=OPERATION_ID,
        parent_resource_id=parent_resource_id,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        success_status=200,
        mutate=winner_mutate,
    )
    assert winner_calls == 1

    # 패자가 find 시점에는 아직 winner의 record를 보지 못했던 상황을 재현하기 위해,
    # find_sync_idempotency_record의 첫 호출만 None으로 만들고 이후 호출(경쟁 처리 중
    # 재조회)은 실제 조회로 되돌린다.
    original_find = repository.find_sync_idempotency_record
    call_count = 0

    async def find_once_missing(**kwargs: object) -> IdempotencyRecord | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return await original_find(**kwargs)

    repository.find_sync_idempotency_record = find_once_missing  # type: ignore[method-assign]

    loser_calls = 0
    side_effect_email = f"loser-side-effect-{uuid4().hex[:8]}@example.com"

    async def loser_mutate() -> dict[str, object]:
        nonlocal loser_calls
        loser_calls += 1
        await _create_user_with_email(db_session, side_effect_email)
        return {"identification_id": "loser"}

    loser_result = await service.execute(
        user_id=user.id,
        operation_id=OPERATION_ID,
        parent_resource_id=parent_resource_id,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        success_status=200,
        mutate=loser_mutate,
    )

    assert loser_calls == 1
    assert loser_result.is_replay is True
    assert loser_result.response_body == winner_result.response_body
    assert await _count_idempotency_records(db_session) == 1
    side_effect = await db_session.execute(select(User).where(User.email == side_effect_email))
    assert side_effect.scalar_one_or_none() is None


def test_fernet_snapshot_cipher_round_trips_plaintext() -> None:
    cipher = FernetSnapshotCipher(key=REAL_FERNET_KEY, key_version="v1")

    ciphertext, key_version = cipher.encrypt(b"canonical-json-payload")

    assert key_version == "v1"
    assert ciphertext != b"canonical-json-payload"
    assert cipher.decrypt(ciphertext, key_version="v1") == b"canonical-json-payload"


def test_fernet_snapshot_cipher_rejects_mismatched_key_version() -> None:
    cipher = FernetSnapshotCipher(key=REAL_FERNET_KEY, key_version="v1")
    ciphertext, _ = cipher.encrypt(b"payload")

    with pytest.raises(InvalidToken):
        cipher.decrypt(ciphertext, key_version="v2")


def test_get_default_snapshot_cipher_uses_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY", REAL_FERNET_KEY)
    monkeypatch.setattr(config, "IDEMPOTENCY_SNAPSHOT_ENCRYPTION_KEY_VERSION", "v1")

    cipher = get_default_snapshot_cipher()
    ciphertext, key_version = cipher.encrypt(b"payload")

    assert key_version == "v1"
    assert cipher.decrypt(ciphertext, key_version="v1") == b"payload"


async def _create_user_with_email(session: AsyncSession, email: str) -> User:
    user = User(
        email=email,
        hashed_password="hashed-password",
        name="부작용 확인용 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    return user
