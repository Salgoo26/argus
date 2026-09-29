"""비밀번호 해시 — argon2id (CLAUDE.md 3절 #11)

argon2-cffi의 PasswordHasher 기본값은 argon2id + RFC 9106 권장 저메모리 프로필이다.
"""

import secrets

from argon2 import PasswordHasher

_hasher = PasswordHasher()


def unusable_password_hash() -> str:
    """아무도 모르는 무작위 비밀번호의 해시 — 계정은 있지만 로그인할 수 없는 상태를 만든다.

    취급자 동기화(②)로 생기는 A5 계정의 초기값. 실제 비밀번호는 M4에서 관리 스크립트로 설정한다
    (api-spec 7절 "A5 초기 비밀번호 전달 방식" — 2026-09-29 구현 결정).
    형식이 정상인 해시라 로그인 코드가 이 경우를 따로 처리할 필요가 없다.
    """
    return _hasher.hash(secrets.token_urlsafe(32))
