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
