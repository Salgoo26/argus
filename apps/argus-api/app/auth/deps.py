"""Argus 사용자 인증 의존성 — 화면용 API마다 Depends(current_user)로 건다

인증된 사용자를 request.state.user에 두고, 자체 접속기록 기록지에 식별자(§2 3호)를 적는다.
역할: OFFICER(정보보호 담당자, A4) / HANDLER(개인정보취급자, A5 — 본인 탐지건만)
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select

from app.agent import record_actor
from app.auth.tokens import COOKIE_NAME, issue_token, read_token
from app.config import Settings
from app.errors import ApiError
from app.models import argus_user, handler

MAX_FAILED_LOGINS = 5  # 연속 실패 5회 → LOCKED (policy 4-3)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    login_id: str
    role: str
    handler_id: int | None
    handler_name: str | None


def user_query():
    # HANDLER는 연결된 취급자 명부의 재직 상태까지 함께 본다 (퇴직 = 로그인 불가)
    return select(
        argus_user.c.id,
        argus_user.c.login_id,
        argus_user.c.password_hash,
        argus_user.c.role,
        argus_user.c.status,
        argus_user.c.failed_login_count,
        argus_user.c.handler_id,
        handler.c.name.label("handler_name"),
        handler.c.employment_status,
    ).outerjoin(handler, handler.c.id == argus_user.c.handler_id)


def block_reason(row) -> str | None:
    """로그인·요청을 막아야 하면 오류 코드, 아니면 None"""
    if row["status"] == "DISABLED":
        return "ACCOUNT_DISABLED"
    if row["status"] == "LOCKED":
        return "ACCOUNT_LOCKED"
    # 퇴직 동기화로 계정이 DISABLED가 되지만, 명부 상태도 함께 확인한다(이중 확인)
    if row["role"] == "HANDLER" and row["employment_status"] != "ACTIVE":
        return "ACCOUNT_DISABLED"
    return None


def current_user(request: Request) -> AuthenticatedUser:
    settings: Settings = request.app.state.settings
    secret: bytes = request.app.state.auth_secret

    user_id = read_token(request.cookies.get(COOKIE_NAME), secret)
    if user_id is None:
        raise ApiError(401, "UNAUTHENTICATED", "login required")
    with request.app.state.engine.connect() as conn:
        row = conn.execute(user_query().where(argus_user.c.id == user_id)).mappings().first()
    # 토큰이 유효해도 로그인 이후 잠김·비활성·퇴직이면 즉시 차단
    if row is None or block_reason(row) is not None:
        raise ApiError(401, "UNAUTHENTICATED", "login required")

    user = AuthenticatedUser(
        row["id"], row["login_id"], row["role"], row["handler_id"], row["handler_name"]
    )
    request.state.user = user
    record_actor(user.login_id)  # 자체 접속기록 식별자 (§2 3호)
    # 30분 미사용 만료를 요청마다 연장 — 쿠키는 main.py의 미들웨어가 응답에 싣는다
    request.state.session_token = issue_token(user.id, secret, settings.session_idle_minutes)
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
