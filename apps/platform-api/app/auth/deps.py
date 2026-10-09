"""관리자 인증 의존성 — 관리자 라우트마다 Depends(current_operator)로 건다.

Spring Security의 SecurityContext처럼, 인증된 취급자를 request.state.operator에 두고
접속기록 기록지에 "식별자"(§2 3호)를 적는다.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select

from app.agent import record_actor
from app.auth.tokens import ADMIN, issue_token, read_token
from app.config import Settings
from app.errors import ApiError
from app.models import operator

MAX_FAILED_LOGINS = 5  # 연속 실패 5회 → 잠금 (implementation-log 2026-09-29 설계 변경 3)


@dataclass(frozen=True)
class AuthenticatedOperator:
    id: int
    login_id: str
    name: str
    team: str
    role: str


def is_blocked(employment_status: str, failed_login_count: int) -> bool:
    return employment_status != "ACTIVE" or failed_login_count >= MAX_FAILED_LOGINS


def _unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "login required")


def current_operator(request: Request) -> AuthenticatedOperator:
    settings: Settings = request.app.state.settings
    secret: bytes = request.app.state.auth_secret

    operator_id = read_token(request.cookies.get(ADMIN.cookie_name), secret, ADMIN)
    if operator_id is None:
        raise _unauthenticated()

    with request.app.state.engine.connect() as conn:
        row = conn.execute(select(operator).where(operator.c.id == operator_id)).mappings().first()
    # 토큰이 유효해도 로그인 이후 퇴직·잠금됐으면 즉시 차단
    if row is None or is_blocked(row["employment_status"], row["failed_login_count"]):
        raise _unauthenticated()

    authenticated = AuthenticatedOperator(
        row["id"], row["login_id"], row["name"], row["team"], row["role"]
    )
    request.state.operator = authenticated
    record_actor(authenticated.login_id)  # 접속기록 식별자 (§2 3호)
    # 30분 미사용 만료를 요청마다 연장 — 쿠키는 main.py의 미들웨어가 응답에 싣는다
    # (토큰 용도(aud)를 관리자용으로 — 고객 토큰과 바꿔 쓸 수 없게)
    # (핸들러가 Response를 직접 돌려주는 CSV 다운로드에도 빠짐없이 붙이기 위해)
    request.state.session_cookie = (
        ADMIN,
        issue_token(authenticated.id, secret, settings.session_idle_minutes, ADMIN),
    )
    return authenticated


CurrentOperator = Annotated[AuthenticatedOperator, Depends(current_operator)]


def optional_operator(request: Request) -> AuthenticatedOperator | None:
    """인증돼 있으면 그 취급자, 아니면 None — 비인증 요청도 받아야 하는 라우트(로그아웃)용.
    인증된 경우에만 current_operator가 기록지에 식별자를 적으므로, 비인증 요청은 기록되지 않는다."""
    try:
        return current_operator(request)
    except ApiError:
        return None


OptionalOperator = Annotated[AuthenticatedOperator | None, Depends(optional_operator)]
