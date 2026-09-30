"""관리자 로그인 토큰 — JWT(HS256)를 HttpOnly 쿠키로 (implementation-log 2026-09-29 설계 변경 2)

- 서버에 세션을 저장하지 않는다: 서명으로 "내가 발급한 토큰"인지 확인
- 만료는 마지막 요청 기준 30분 — 요청마다 새 토큰으로 갈아 끼운다(sliding).
  안전성 확보조치 기준 "일정 시간 업무처리를 하지 않으면 접속 차단"에 대응
- 토큰에는 operator id만 담는다. 이름·권한은 매 요청 DB에서 다시 읽으므로
  퇴직·잠금이 즉시 반영된다
- 알려진 한계: 로그아웃해도 토큰 자체는 만료 전까지 유효(쿠키만 지운다) — 보안성 검토 후보
"""

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Response

from app.config import Settings

COOKIE_NAME = "platform_session"
ALGORITHM = "HS256"


def issue_token(operator_id: int, secret: bytes, idle_minutes: int) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(operator_id), "iat": now, "exp": now + timedelta(minutes=idle_minutes)}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def read_token(token: str | None, secret: bytes) -> int | None:
    """유효하면 operator id, 아니면(없음·위조·만료·형식 오류) None"""
    if not token:
        return None
    try:
        # algorithms를 고정 — 토큰 헤더의 alg(none, 다른 알고리즘)를 믿지 않는다
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
