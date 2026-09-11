import hashlib
import secrets

from passlib.context import CryptContext

# 회원가입/로그인 Backend 계약: 비밀번호는 Argon2 해시로 저장·검증합니다.
# 기존에 bcrypt로 저장된 값도 검증은 가능하도록 남겨두되, 새로 생성되는 해시는 Argon2를 사용합니다.
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def generate_password_reset_token() -> str:
    """PD-206 결정 3: 재설정 링크에 담을 고엔트로피 원문 token입니다. 사용자가 고르는
    비밀번호가 아니라 서버가 생성하는 난수라, 느린 password hash가 아니라 빠른 다이제스트로
    충분합니다(아래 hash_password_reset_token 참고)."""
    return secrets.token_urlsafe(32)


def hash_password_reset_token(raw_token: str) -> str:
    """원문은 DB에 저장하지 않고 이 다이제스트만 저장합니다(DB 유출 시 재사용 방지)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_email_verification_token() -> str:
    """회원가입 전 이메일 인증 링크에 담을 고엔트로피 원문 token입니다."""
    return secrets.token_urlsafe(32)


def hash_email_verification_token(raw_token: str) -> str:
    """원문 이메일 인증 token은 저장하지 않고 SHA-256 hex digest만 저장합니다."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
