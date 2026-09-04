import asyncio
import contextlib
from uuid import UUID, uuid4

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.security import hash_password
from app.dtos.auth import LoginRequest
from app.models.profiles import Profile
from app.models.users import User
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
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()
    finally:
        await session.close()


async def _is_login_blocked_on_user_row_lock() -> bool:
    """`login()`이 `update_last_login()`(UPDATE) 또는 `get_user_for_update()`
    (`SELECT ... FOR UPDATE`) 중 어느 단계에서 대기하든, `user` 테이블 관련 쿼리가 lock을
    기다리는 상태인지로 감지합니다 — 정확히 어느 문장인지는 이 테스트의 관심사가 아닙니다."""
    session = AsyncSession(bind=test_engine, expire_on_commit=False)
    try:
        result = await session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_stat_activity
                    WHERE wait_event_type = 'Lock'
                      AND query ILIKE '%"user"%'
                )
                """
            )
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
        authenticated_user = await AuthService(UserRepository(login_session)).authenticate(
            LoginRequest(email=user.email, password=_TEST_PASSWORD)
        )
        assert authenticated_user.token_version == 0

        login_task = asyncio.create_task(AuthService(UserRepository(login_session)).login(authenticated_user))

        blocked = False
        for _ in range(100):
            if await _is_login_blocked_on_user_row_lock():
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
