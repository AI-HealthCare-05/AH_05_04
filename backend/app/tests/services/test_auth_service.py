import asyncio
import contextlib
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ApiError, ErrorDetail
from app.core.utils.security import (
    generate_password_reset_token,
    hash_password,
    hash_password_reset_token,
)
from app.dtos.auth import LoginRequest
from app.models.password_reset import PasswordResetToken
from app.models.profiles import Profile
from app.models.refresh_session import RefreshSession
from app.models.users import User
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.refresh_session_repository import RefreshSessionRepository
from app.repositories.user_repository import UserRepository
from app.services.auth import AuthService
from app.tests.conftest import test_engine

_TEST_PASSWORD = "Password123!"


async def _create_committed_user(*, email: str) -> User:
    """savepoint 격리로는 실제 동시성을 재현할 수 없으므로, 이 파일의 테스트는 이 헬퍼로
    test DB에 직접 commit된 User를 만들고 별도 세션 여러 개로 동시 접근을 재현합니다."""
    session = AsyncSession(bind=test_engine, expire_on_commit=False)
    try:
        user = User(
            email=email,
            hashed_password=hash_password(_TEST_PASSWORD),
            name="로그인경쟁테스터",
        )
        session.add(user)
        await session.commit()
        return user
    finally:
        await session.close()


async def _delete_user(user_id: UUID) -> None:
    session = AsyncSession(bind=test_engine, expire_on_commit=False)
    try:
        await session.execute(delete(Profile).where(Profile.user_id == user_id))
        await session.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        await session.execute(delete(RefreshSession).where(RefreshSession.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()
    finally:
        await session.close()


async def _is_blocked_on_query_matching(query_substring: str) -> bool:
    """대기 중인 쿼리 텍스트에 `query_substring`이 포함된 요청이 lock을 기다리는
    상태인지로 감지합니다 — 이 파일의 동시성 테스트(로그인/재설정/refresh rotation)가
    공유합니다. `user`는 예약어라 쿼리에 큰따옴표로 인용되지만(`"user"`),
    `refresh_session`은 그렇지 않으므로 호출자가 정확한 부분 문자열을 넘긴다."""
    session = AsyncSession(bind=test_engine, expire_on_commit=False)
    try:
        result = await session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_stat_activity
                    WHERE wait_event_type = 'Lock'
                      AND query ILIKE :pattern
                )
                """
            ),
            {"pattern": f"%{query_substring}%"},
        )
        return bool(result.scalar_one())
    finally:
        await session.close()


async def test_login_waits_for_concurrent_logout_and_issues_latest_token_version() -> None:
    """authenticate()가 로그인 흐름 맨 앞에서 읽은 `user.token_version`을 그대로 써서 토큰을
    발급하면, 그 사이 커밋되는 동시 로그아웃의 `token_version` 증가를 놓쳐 이미 무효인
    access/refresh token을 발급할 수 있습니다(리뷰 지적). `AuthService.login()`이 토큰 발급
    직전 row lock으로 다시 읽어, 동시 로그아웃의 커밋을 기다렸다가 최신 `token_version`으로
    발급하는지 검증합니다.

    실제 로그인 요청처럼 `authenticate()`와 `login()`을 **같은 세션**에서 순서대로 호출해야
    합니다 — 별도 세션에서 만든 detached `User`를 `login()`에 직접 넘기면 `get_user_for_update()`가
    그 세션의 identity map을 처음 채우는 조회가 되어, `authenticate()`가 이미 그 자리에
    낡은 값을 올려둔 실제 상황(SQLAlchemy identity map 재사용 문제)을 재현하지 못합니다(리뷰
    지적)."""
    # `LoginRequest`가 `EmailStr`로 검증하므로(email-validator가 `.local`을 특수 예약
    # 도메인으로 거부함) 다른 auth_apis 테스트와 같은 `example.com`을 씁니다.
    user = await _create_committed_user(email=f"login-race-{uuid4().hex[:10]}@example.com")

    logout_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    login_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    login_task: asyncio.Task | None = None
    tokens = None
    try:
        # 로그아웃 트랜잭션이 token_version 증가를 커밋 전 상태로 유지해, 아직 진행 중인
        # 동시 로그아웃을 재현합니다.
        await UserRepository(logout_session).increment_token_version(user)
        await logout_session.flush()

        # 실제 로그인 요청처럼 authenticate()가 login_session의 identity map에 이 사용자를
        # 먼저 로드합니다 — 아직 로그아웃이 commit 전이라(READ COMMITTED) token_version=0으로
        # 보입니다. populate_existing 없이는 이 객체가 뒤의 SELECT ... FOR UPDATE 결과에도
        # 그대로 재사용되어 낡은 값을 반환하는 버그가 재현됩니다.
        login_auth_service = AuthService(
            UserRepository(login_session),
            PasswordResetRepository(login_session),
            RefreshSessionRepository(login_session),
        )
        authenticated_user = await login_auth_service.authenticate(
            LoginRequest(email=user.email, password=_TEST_PASSWORD)
        )
        assert authenticated_user.token_version == 0

        login_task = asyncio.create_task(login_auth_service.login(authenticated_user, password=_TEST_PASSWORD))

        blocked = False
        for _ in range(100):
            if await _is_blocked_on_query_matching('"user"'):
                blocked = True
                break
            await asyncio.sleep(0.05)

        assert not login_task.done()
        assert blocked, "로그인이 row lock에서 대기하지 않았습니다."

        await logout_session.commit()

        tokens = await asyncio.wait_for(login_task, timeout=10)
        login_task = None
        await login_session.commit()
    finally:
        if logout_session.in_transaction():
            await logout_session.rollback()
        if login_task is not None:
            login_task.cancel()
            with contextlib.suppress(BaseException):
                await login_task
        await login_session.close()
        await logout_session.close()
        await _delete_user(user.id)

    assert tokens is not None
    assert tokens["access_token"]["token_version"] == 1
    assert tokens["refresh_token"]["token_version"] == 1


async def test_login_rejects_when_password_reset_commits_before_lock_is_acquired() -> None:
    """PR #404 후속 리뷰: `login()`이 row lock 획득 후 `token_version`만 다시 읽고
    비밀번호는 재검증하지 않으면, `authenticate()`가 이전(곧 무효화될) 비밀번호로
    통과한 뒤 그 사이 비밀번호 재설정이 커밋돼도 최신 `token_version`으로 유효한
    토큰이 발급된다 — 이전 비밀번호를 아는 요청이 재설정 이후에도 접근을 유지해
    재설정의 보안 목적을 무력화한다. `login()`이 lock 획득 후 현재 비밀번호로
    다시 검증해 거부하는지, 진짜 두 DB 커넥션의 경쟁으로 검증한다."""
    user = await _create_committed_user(email=f"login-reset-race-{uuid4().hex[:10]}@example.com")

    reset_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    login_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    login_task: asyncio.Task | None = None
    login_error: ApiError | None = None
    try:
        # 재설정 트랜잭션이 비밀번호 변경 + token_version 증가를 커밋 전 상태로
        # 유지해, 아직 진행 중인 동시 재설정을 재현한다.
        reset_user = await UserRepository(reset_session).get_user_for_update(user.id)
        assert reset_user is not None
        reset_user.hashed_password = hash_password("NewPassword456!")
        await UserRepository(reset_session).increment_token_version(reset_user)
        await reset_session.flush()

        # 실제 로그인 요청처럼 이전(곧 무효화될) 비밀번호로 authenticate()가 먼저
        # 통과한다 — 아직 재설정이 commit 전이라(READ COMMITTED) 이전 비밀번호가
        # 여전히 유효하게 보인다.
        login_auth_service = AuthService(
            UserRepository(login_session),
            PasswordResetRepository(login_session),
            RefreshSessionRepository(login_session),
        )
        authenticated_user = await login_auth_service.authenticate(
            LoginRequest(email=user.email, password=_TEST_PASSWORD)
        )

        login_task = asyncio.create_task(login_auth_service.login(authenticated_user, password=_TEST_PASSWORD))

        blocked = False
        for _ in range(100):
            if await _is_blocked_on_query_matching('"user"'):
                blocked = True
                break
            await asyncio.sleep(0.05)

        assert not login_task.done()
        assert blocked, "로그인이 row lock에서 대기하지 않았습니다."

        await reset_session.commit()

        try:
            await asyncio.wait_for(login_task, timeout=10)
        except ApiError as exc:
            login_error = exc
        login_task = None
    finally:
        if reset_session.in_transaction():
            await reset_session.rollback()
        if login_task is not None:
            login_task.cancel()
            with contextlib.suppress(BaseException):
                await login_task
        await login_session.close()
        await reset_session.close()
        await _delete_user(user.id)

    assert login_error is not None
    assert login_error.status_code == 401


async def test_reset_password_concurrent_confirm_only_succeeds_once() -> None:
    """PD-206 결정 3: 같은 token으로 거의 동시에 두 요청이 와도 정확히 하나만 성공해야
    한다. `reset_password()`가 user row를 먼저 잠근 뒤 token 유효성을 재확인하는 순서가
    진짜 두 DB 커넥션의 동시 요청에서도 직렬화되는지 검증한다(savepoint 격리로는 이
    race를 재현할 수 없다)."""
    user = await _create_committed_user(email=f"reset-race-{uuid4().hex[:10]}@example.com")

    winner_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    loser_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    loser_task: asyncio.Task | None = None
    loser_error: ApiError | None = None
    try:
        raw_token = generate_password_reset_token()
        await PasswordResetRepository(winner_session).create_token(
            user_id=user.id,
            token_hash=hash_password_reset_token(raw_token),
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=config.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        )
        await winner_session.commit()

        # winner가 실제 소비 직전 상태(user row lock 보유, 아직 커밋 전)를 만들어
        # 아직 끝나지 않은 동시 요청을 재현한다.
        winner_user = await UserRepository(winner_session).get_user_for_update(user.id)
        assert winner_user is not None

        loser_service = AuthService(
            UserRepository(loser_session),
            PasswordResetRepository(loser_session),
            RefreshSessionRepository(loser_session),
        )
        loser_task = asyncio.create_task(
            loser_service.reset_password(token=raw_token, new_password="LoserPassword456!")
        )

        blocked = False
        for _ in range(100):
            if await _is_blocked_on_query_matching('"user"'):
                blocked = True
                break
            await asyncio.sleep(0.05)

        assert not loser_task.done()
        assert blocked, "동시 재설정 요청이 row lock에서 대기하지 않았습니다."

        # winner가 실제로 이 token을 소비하고 커밋한다 — loser는 그 뒤에 깨어나 이미
        # 소비된 token을 보게 된다.
        winner_tokens = await PasswordResetRepository(winner_session).lock_unused_unexpired_tokens_for_user(
            winner_user.id
        )
        assert len(winner_tokens) == 1
        await PasswordResetRepository(winner_session).mark_tokens_used(
            winner_tokens, used_at=datetime.now(config.TIMEZONE)
        )
        winner_user.hashed_password = hash_password("WinnerPassword456!")
        await UserRepository(winner_session).increment_token_version(winner_user)
        await winner_session.commit()

        try:
            await asyncio.wait_for(loser_task, timeout=10)
        except ApiError as exc:
            loser_error = exc
        loser_task = None
    finally:
        if loser_task is not None:
            loser_task.cancel()
            with contextlib.suppress(BaseException):
                await loser_task
        if winner_session.in_transaction():
            await winner_session.rollback()
        await winner_session.close()
        await loser_session.close()
        await _delete_user(user.id)

    assert loser_error is not None
    assert loser_error.details == [ErrorDetail(field="token", reason="RESET_TOKEN_INVALID", rejected_value=None)]


async def test_request_password_reset_concurrent_requests_only_create_one_token() -> None:
    """PR #404 리뷰: user row를 잠그지 않고 쿨다운을 조회하면, 거의 동시에 온 두
    요청이 둘 다 "최근 토큰 없음"을 보고 각각 커밋해 60초 쿨다운을 우회한 중복
    토큰을 만들어낼 수 있었다. `request_password_reset()`이 대상 user row를 먼저
    `FOR UPDATE`로 잠가 쿨다운 조회~토큰 생성을 이 사용자 기준으로 직렬화하는지,
    진짜 두 DB 커넥션의 동시 요청에서도 검증한다(savepoint 격리로는 이 race를
    재현할 수 없다)."""
    user = await _create_committed_user(email=f"cooldown-race-{uuid4().hex[:10]}@example.com")

    winner_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    loser_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    loser_task: asyncio.Task | None = None
    loser_result: str | None = None
    try:
        # winner가 request_password_reset()과 동일하게 user row를 먼저 잠근 뒤
        # 아직 커밋 전인 상태(row lock 보유)를 만들어, 아직 끝나지 않은 동시
        # 요청을 재현한다.
        winner_user = await UserRepository(winner_session).get_user_for_update(user.id)
        assert winner_user is not None

        loser_service = AuthService(
            UserRepository(loser_session),
            PasswordResetRepository(loser_session),
            RefreshSessionRepository(loser_session),
        )
        loser_task = asyncio.create_task(loser_service.request_password_reset(user.email))

        blocked = False
        for _ in range(100):
            if await _is_blocked_on_query_matching('"user"'):
                blocked = True
                break
            await asyncio.sleep(0.05)

        assert not loser_task.done()
        assert blocked, "동시 재설정 요청이 row lock에서 대기하지 않았습니다."

        # winner가 실제로 쿨다운 조회 + 토큰 생성을 수행하고 커밋한다 — loser는
        # 그 뒤에 깨어나 이미 발급된 토큰을 "최근 토큰 있음"으로 봐야 한다.
        cooldown_since = datetime.now(config.TIMEZONE) - timedelta(
            seconds=config.PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS
        )
        winner_recent_token = await PasswordResetRepository(winner_session).find_recent_token_for_user(
            user_id=winner_user.id, since=cooldown_since
        )
        assert winner_recent_token is None
        await PasswordResetRepository(winner_session).create_token(
            user_id=winner_user.id,
            token_hash=hash_password_reset_token(generate_password_reset_token()),
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=config.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        )
        await winner_session.commit()

        loser_result = await asyncio.wait_for(loser_task, timeout=10)
        loser_task = None
    finally:
        if loser_task is not None:
            loser_task.cancel()
            with contextlib.suppress(BaseException):
                await loser_task
        if winner_session.in_transaction():
            await winner_session.rollback()
        await winner_session.close()
        await loser_session.close()

        async with AsyncSession(bind=test_engine, expire_on_commit=False) as verify_session:
            token_count_result = await verify_session.execute(
                text("SELECT count(*) FROM password_reset_token WHERE user_id = :user_id"),
                {"user_id": str(user.id)},
            )
            token_count = token_count_result.scalar_one()

        await _delete_user(user.id)

    # winner가 이미 토큰을 만들어 커밋했으므로, loser는 쿨다운 중으로 처리돼 새
    # 토큰을 만들지 않아야 한다(둘 다 생성되는 lost update가 아니어야 한다).
    assert loser_result is None
    assert token_count == 1


async def test_rotate_jti_concurrent_same_expected_jti_only_one_succeeds() -> None:
    """#206: 같은 refresh token(jti)으로 거의 동시에 두 rotation 요청이 와도 정확히
    하나만 성공해야 한다. `RefreshSessionRepository.rotate_jti()`의 UPDATE 기반 CAS가
    진짜 두 DB 커넥션의 동시 요청에서도 lost update 없이 직렬화되는지 검증한다."""
    user = await _create_committed_user(email=f"rotate-race-{uuid4().hex[:10]}@example.com")
    session_id = uuid4()
    original_jti = uuid4().hex

    setup_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    first_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    second_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    second_task: asyncio.Task | None = None
    second_result: bool | None = None
    try:
        await RefreshSessionRepository(setup_session).create_session(
            session_id=session_id, user_id=user.id, jti=original_jti
        )
        await setup_session.commit()

        # first가 실제로 UPDATE를 적용했지만 아직 커밋 전인 상태(row lock 보유)를 만들어
        # 아직 끝나지 않은 동시 rotation을 재현한다.
        first_result = await RefreshSessionRepository(first_session).rotate_jti(
            session_id=session_id, expected_jti=original_jti, new_jti=uuid4().hex
        )
        assert first_result is True

        second_task = asyncio.create_task(
            RefreshSessionRepository(second_session).rotate_jti(
                session_id=session_id, expected_jti=original_jti, new_jti=uuid4().hex
            )
        )

        blocked = False
        for _ in range(100):
            if await _is_blocked_on_query_matching("refresh_session"):
                blocked = True
                break
            await asyncio.sleep(0.05)

        assert not second_task.done()
        assert blocked, "동시 rotation 요청이 row lock에서 대기하지 않았습니다."

        await first_session.commit()

        second_result = await asyncio.wait_for(second_task, timeout=10)
        second_task = None
        await second_session.commit()
    finally:
        if second_task is not None:
            second_task.cancel()
            with contextlib.suppress(BaseException):
                await second_task
        if first_session.in_transaction():
            await first_session.rollback()
        await first_session.close()
        await second_session.close()
        await setup_session.close()
        await _delete_user(user.id)

    # first가 이미 jti를 교체해 커밋했으므로, second는 같은 expected_jti로 더 이상
    # 일치시킬 수 없어 실패해야 한다(둘 다 성공하는 lost update가 아니어야 한다).
    assert second_result is False
