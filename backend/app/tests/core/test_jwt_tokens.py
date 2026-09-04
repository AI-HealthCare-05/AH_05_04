from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core import config
from app.core.jwt.tokens import AccessToken, RefreshToken
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


def test_refresh_token_exp_matches_configured_minutes_not_days() -> None:
    """`REFRESH_TOKEN_EXPIRE_MINUTES`는 이름 그대로 "분" 단위 값(기본 14*24*60=20160)인데
    `RefreshToken.lifetime`이 `timedelta(days=...)`로 이 값을 "일"로 잘못 해석해, 실제 발급되는
    refresh token이 설정된 14일이 아니라 약 55년짜리로 만들어지는 버그가 있었다(4차 리뷰 지적).
    `token_version`이 로그아웃 등으로 바뀌지 않는 한 탈취된 토큰이 사실상 영구히 유효해지는
    보안 문제였다. 실제 `exp`는 발급 시각 + 설정된 분(14일)과 일치해야 하며, 55년 뒤가 되면
    안 된다."""
    user = _build_user()
    before_issue = datetime.now(UTC)

    token = RefreshToken.for_user(user)

    after_issue = datetime.now(UTC)
    exp = datetime.fromtimestamp(token.payload["exp"], tz=UTC)

    expected_earliest = before_issue + timedelta(minutes=config.REFRESH_TOKEN_EXPIRE_MINUTES) - timedelta(seconds=1)
    expected_latest = after_issue + timedelta(minutes=config.REFRESH_TOKEN_EXPIRE_MINUTES)

    assert expected_earliest <= exp <= expected_latest
    # 회귀 방지: 버그가 재발하면(다시 `days=`로 바뀌면) 이 값이 수백~수만 배 커집니다.
    assert exp < before_issue + timedelta(days=30)


def test_refresh_token_derived_access_token_keeps_its_own_lifetime() -> None:
    """`RefreshToken.access_token` 프로퍼티가 `/auth/token/refresh`에서 새 access token을
    만들 때 쓰인다. refresh token의 수명 계산을 고치면서 이 프로퍼티가 만드는 access token의
    수명(`ACCESS_TOKEN_EXPIRE_MINUTES`, 기본 60분)까지 실수로 영향받지 않는지 확인한다."""
    user = _build_user()
    refresh_token = RefreshToken.for_user(user)

    before_issue = datetime.now(UTC)
    derived_access_token = refresh_token.access_token
    after_issue = datetime.now(UTC)

    exp = datetime.fromtimestamp(derived_access_token.payload["exp"], tz=UTC)
    expected_earliest = before_issue + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES) - timedelta(seconds=1)
    expected_latest = after_issue + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)

    assert expected_earliest <= exp <= expected_latest
