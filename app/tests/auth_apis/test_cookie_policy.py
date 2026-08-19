import pytest

from app.apis.v1.auth_routers import should_use_secure_cookie
from app.core.config import Env


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (Env.LOCAL, False),
        (Env.STAGING, True),
        (Env.PRODUCTION, True),
    ],
)
def test_secure_cookie_policy(
    env: Env,
    expected: bool,
) -> None:
    assert should_use_secure_cookie(env) is expected
