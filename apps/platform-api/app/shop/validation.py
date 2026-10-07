"""고객 입력 형식 — 회원가입·마이페이지 공통

비밀번호 규칙: 10자 이상, 영문·숫자·특수문자 중 2종류 이상
(KISA 「암호이용 안내서」의 "2종류 이상 조합 시 최소 10자리" 기준).
상한 256자는 관리자 로그인과 같다 — 초대형 입력으로 해시 시간을 늘리는 공격 방지.
"""

import re
from typing import Annotated

from pydantic import AfterValidator, Field

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# \d는 다른 문자권의 숫자(예: 아라비아-인도 숫자)도 받으므로 0-9로 한정
_PHONE = re.compile(r"^01[016789]-?[0-9]{3,4}-?[0-9]{4}$")
_ZIP_CODE = re.compile(r"^[0-9]{5}$")  # 우편번호(국가기초구역번호) 5자리
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


def _phone(value: str) -> str:
    value = value.strip()
    if not _PHONE.match(value):
        raise ValueError("invalid phone")
    digits = value.replace("-", "")
    return f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}"


def _optional_text(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def _name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("empty name")
    return value


def _zip_code(value: str) -> str:
    value = value.strip()
    if not _ZIP_CODE.match(value):
        raise ValueError("invalid zip code")
    return value


Email = Annotated[str, Field(min_length=3, max_length=255), AfterValidator(_email)]
Password = Annotated[str, Field(min_length=1, max_length=256)]
NewPassword = Annotated[str, Field(max_length=256), AfterValidator(_new_password)]
Name = Annotated[str, Field(min_length=1, max_length=50), AfterValidator(_name)]
# 휴대폰은 필수 (2026-10-07 플랫폼 보강, policy 4-3) — 주문·배송 연락용. 빈 값은 형식 오류로 거부
Phone = Annotated[str, Field(min_length=1, max_length=20), AfterValidator(_phone)]
# 배송지 (기능 레이어 7-4 ②) — 받는 사람은 Name, 연락처는 Phone과 같은 규칙
Label = Annotated[str, Field(min_length=1, max_length=30), AfterValidator(_name)]
ZipCode = Annotated[str, Field(min_length=5, max_length=10), AfterValidator(_zip_code)]
AddressLine = Annotated[str, Field(min_length=1, max_length=255), AfterValidator(_name)]
AddressDetail = Annotated[
    str | None, Field(default=None, max_length=100), AfterValidator(_optional_text)
]
