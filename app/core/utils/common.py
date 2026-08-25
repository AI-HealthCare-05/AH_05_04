import re


def normalize_email(email: str) -> str:
    """이메일 저장·조회 기준을 소문자로 통일합니다.

    PostgreSQL의 일반 VARCHAR unique 인덱스는 대소문자를 구분하므로
    Repository에 전달하기 전에 같은 정규화 규칙을 적용합니다.
    """
    return email.lower()


def normalize_phone_number(phone_number: str) -> str:
    if phone_number.startswith("+82"):
        phone_number = "0" + phone_number[3:]

    phone_number = re.sub(r"\D", "", phone_number)
    return phone_number
