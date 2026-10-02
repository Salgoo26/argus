"""고객 인증 의존성 — 고객 라우트마다 Depends(current_member)로 건다.

관리자 쪽(auth/deps.py)과 같은 구조지만 쿠키·토큰 용도(aud)가 다르다 — 고객 토큰으로는
관리자 API를, 관리자 토큰으로는 고객 API를 쓸 수 없다.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select

from app.agent.client_ip import resolve_client_ip
from app.auth.tokens import CUSTOMER, issue_token, read_token
from app.config import Settings
from app.errors import ApiError
from app.models import member


@dataclass(frozen=True)
class AuthenticatedMember:
    id: int
    email: str
    name: str


def _unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "login required")


def current_member(request: Request) -> AuthenticatedMember:
    settings: Settings = request.app.state.settings
    secret: bytes = request.app.state.auth_secret

    member_id = read_token(request.cookies.get(CUSTOMER.cookie_name), secret, CUSTOMER)
    if member_id is None:
        raise _unauthenticated()

    with request.app.state.engine.connect() as conn:
        row = (
            conn.execute(
                select(member.c.id, member.c.email, member.c.name, member.c.status).where(
                    member.c.id == member_id
                )
            )
            .mappings()
            .first()
        )
    # 탈퇴(행 삭제)했으면 토큰이 남아 있어도 즉시 차단
    if row is None or row["status"] != "ACTIVE":
        raise _unauthenticated()

    request.state.session_cookie = (
        CUSTOMER,
        issue_token(row["id"], secret, settings.session_idle_minutes, CUSTOMER),
    )
    return AuthenticatedMember(row["id"], row["email"], row["name"])


CurrentMember = Annotated[AuthenticatedMember, Depends(current_member)]


def client_ip(request: Request) -> str | None:
    """동의 이력의 접속 IP — 관리자 접속기록과 같은 신뢰 프록시 규칙 (CLAUDE.md 3절 #10)"""
    peer = request.client.host if request.client else ""
    try:
        return resolve_client_ip(
            peer, request.headers.get("x-forwarded-for"), request.app.state.trusted_proxies
        )
    except ValueError:
        return None  # 접속지를 지어내지 않는다 — 비워 둔다
