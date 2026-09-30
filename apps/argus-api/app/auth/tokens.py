"""Argus 로그인 토큰 — JWT(HS256)를 HttpOnly 쿠키로 (policy 4-3, 플랫폼 관리자와 같은 방식)

- 서버에 세션을 저장하지 않는다: 서명으로 "내가 발급한 토큰"인지 확인
- 만료는 마지막 요청 기준 30분 — 요청마다 새 토큰으로 갈아 끼운다(sliding)
- 토큰에는 argus_user id만. 역할·상태는 매 요청 DB에서 다시 읽어 잠금·퇴직이 즉시 반영된다
- 알려진 한계: 로그아웃해도 토큰 자체는 만료 전까지 유효 (architecture 8-6)
"""

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Response

from app.config import Settings

COOKIE_NAME = "argus_session"
ALGORITHM = "HS256"


def issue_token(user_id: int, secret: bytes, idle_minutes: int) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=idle_minutes)}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def read_token(token: str | None, secret: bytes) -> int | None:
    """유효하면 argus_user id, 아니면(없음·위조·만료·형식 오류) None"""
    if not token:
        return None
    try:
        # algorithms 고정 — 토큰 헤더의 alg(none 등)를 믿지 않는다
        claims = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": ["sub", "iat", "exp"]}
        )
        return int(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        return None


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_idle_minutes * 60,
        httponly=True,  # JavaScript에서 읽을 수 없음 → XSS로 토큰을 빼가기 어렵다
        samesite="strict",  # 다른 사이트에서 시작된 요청엔 쿠키가 붙지 않음 → CSRF 방어
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        COOKIE_NAME, httponly=True, samesite="strict", secure=settings.cookie_secure, path="/"
    )
