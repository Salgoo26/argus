"""비밀번호 해시 — argon2id (CLAUDE.md 3절 #11)

argon2-cffi의 PasswordHasher 기본값은 argon2id + RFC 9106 권장 저메모리 프로필이다.
argon2id는 일부러 느리고 메모리를 많이 쓰게 만든 해시라, DB가 유출돼도 무차별 대입 비용이 크다
(Spring Security의 BCryptPasswordEncoder 자리에 Argon2PasswordEncoder를 쓰는 것과 같다).
"""

import secrets
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def unusable_password_hash() -> str:
    """아무도 모르는 무작위 비밀번호의 해시 — 로그인할 수 없는 계정(시드 회원)에 쓴다."""
    return _hasher.hash(secrets.token_urlsafe(32))


@cache
def dummy_password_hash() -> str:
    """존재하지 않는 ID로 로그인할 때도 같은 시간만큼 해시를 검증하기 위한 값.

    없는 ID에는 즉시 실패하고 있는 ID에는 수십 ms 걸리면, 응답 시간만으로
    어떤 ID가 존재하는지 알아낼 수 있다(계정 열거).
    """
    return unusable_password_hash()
