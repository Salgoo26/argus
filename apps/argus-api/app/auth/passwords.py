"""비밀번호 해시 — argon2id (CLAUDE.md 3절 #11)

argon2-cffi의 PasswordHasher 기본값은 argon2id + RFC 9106 권장 저메모리 프로필이다.
"""

import secrets
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def unusable_password_hash() -> str:
    """아무도 모르는 무작위 비밀번호의 해시 — 계정은 있지만 로그인할 수 없는 상태를 만든다.

    취급자 동기화(②)로 생기는 A5 계정의 초기값. 실제 비밀번호는 관리 스크립트
    (app.scripts.users set-password)로 설정한다 (api-spec 3-1 v0.4).
    형식이 정상인 해시라 로그인 코드가 이 경우를 따로 처리할 필요가 없다.
    """
    return _hasher.hash(secrets.token_urlsafe(32))


@cache
def dummy_password_hash() -> str:
    """존재하지 않는 ID로 로그인할 때도 같은 시간만큼 검증하기 위한 값

    응답 시간으로 계정 존재 여부가 드러나지 않게 (계정 열거 방지)
    """
    return unusable_password_hash()
