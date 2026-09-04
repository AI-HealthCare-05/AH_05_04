from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core import config
from app.core.jwt.tokens import AccessToken
from app.models.users import AccountStatus, User


def _build_user() -> User:
    return User(
        id=uuid4(),
        email="jwt-exp-test@example.com",
        hashed_password="hashed",
        name="JWT만료테스터",
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        token_version=0,
    )


def test_access_token_exp_matches_configured_lifetime_in_utc() -> None:
    """`Token.set_exp()`가 `config.TIMEZONE`(Asia/Seoul, UTC+9)의 `from_time`을 받으면,
    `dt.timetuple()`이 tzinfo를 버려 `timegm()`이 그 벽시계 값을 UTC로 오인하는 버그가 있었다.
    그 결과 실제 만료 시각이 의도한 값보다 항상 9시간(TIMEZONE 오프셋) 늦게 계산됐다 — 즉
    설정된 `ACCESS_TOKEN_EXPIRE_MINUTES`보다 실제 토큰 수명이 훨씬 길었다. `exp`는 실제
    발급 시각(UTC 기준) + 설정된 수명과 일치해야 한다."""
    user = _build_user()
    before_issue = datetime.now(UTC)

    token = AccessToken.for_user(user)

    after_issue = datetime.now(UTC)
    exp = datetime.fromtimestamp(token.payload["exp"], tz=UTC)

    # exp는 int(dt.timestamp())로 저장되어 초 단위로 내림(truncate)되므로 1초 여유를 둡니다.
    expected_earliest = before_issue + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES) - timedelta(seconds=1)
    expected_latest = after_issue + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)

    assert expected_earliest <= exp <= expected_latest
