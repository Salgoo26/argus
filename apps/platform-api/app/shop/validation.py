"""고객 입력 형식 — 회원가입·마이페이지 공통

비밀번호 규칙: 10자 이상, 영문·숫자·특수문자 중 2종류 이상
(KISA 「암호이용 안내서」의 "2종류 이상 조합 시 최소 10자리" 기준).
상한 256자는 관리자 로그인과 같다 — 초대형 입력으로 해시 시간을 늘리는 공격 방지.
"""

import re
from typing import Annotated

from pydantic import AfterValidator, Field

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^01[016789]-?\d{3,4}-?\d{4}$")
MIN_PASSWORD_LENGTH = 10


def _email(value: str) -> str:
    value = value.strip().lower()  # 대소문자만 다른 중복 가입 방지
    if not _EMAIL.match(value):
        raise ValueError("invalid email")
    return value


def _new_password(value: str) -> str:
    kinds = sum(
        (
            any(c.isascii() and c.isalpha() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        )
    )
    if len(value) < MIN_PASSWORD_LENGTH or kinds < 2:
        raise ValueError("weak password")
    return value


def _phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    digits = value.strip().replace("-", "")
    if not _PHONE.match(value.strip()):
        raise ValueError("invalid phone")
    return f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}"


def _optional_text(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def _name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("empty name")
    return value


Email = Annotated[str, Field(min_length=3, max_length=255), AfterValidator(_email)]
Password = Annotated[str, Field(min_length=1, max_length=256)]
NewPassword = Annotated[str, Field(max_length=256), AfterValidator(_new_password)]
Name = Annotated[str, Field(min_length=1, max_length=50), AfterValidator(_name)]
Phone = Annotated[str | None, Field(default=None, max_length=20), AfterValidator(_phone)]
Address = Annotated[str | None, Field(default=None, max_length=255), AfterValidator(_optional_text)]
