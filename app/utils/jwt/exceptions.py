class TokenBackendError(Exception):
    pass


class TokenBackendExpiredError(TokenBackendError):
    pass


class TokenError(Exception):
    pass


class ExpiredTokenError(TokenError):
    pass
