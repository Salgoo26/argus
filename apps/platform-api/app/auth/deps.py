"""관리자 인증 의존성 — 관리자 라우트마다 Depends(current_operator)로 건다.

Spring Security의 SecurityContext처럼, 인증된 취급자를 request.state.operator에 둔다.
접속기록 Agent(M2 PR ③)는 여기서 "식별자"(§2 3호)를 가져간다.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select

from app.auth.tokens import COOKIE_NAME, issue_token, read_token
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

    operator_id = read_token(request.cookies.get(COOKIE_NAME), secret)
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
    # 30분 미사용 만료를 요청마다 연장 — 쿠키는 main.py의 미들웨어가 응답에 싣는다
    # (핸들러가 Response를 직접 돌려주는 CSV 다운로드에도 빠짐없이 붙이기 위해)
    request.state.session_token = issue_token(
        authenticated.id, secret, settings.session_idle_minutes
    )
    return authenticated


CurrentOperator = Annotated[AuthenticatedOperator, Depends(current_operator)]
