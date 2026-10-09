"""관리자 인증·권한 의존성 — 관리자 라우트마다 Depends로 건다.

Spring Security의 SecurityContext처럼, 인증된 취급자를 request.state.operator에 두고
접속기록 기록지에 "식별자"(§2 3호)를 적는다.

역할별 접근 범위 (v0.1 보강 L-1 — 고시 §5① 최소 권한):

| 기능                          | ADMIN | OPS | CS | MARKETING |
| 회원 목록·상세·검색           |  O    |  O  | O  |  O        |
| 주문·1:1 문의(답변 포함)      |  O    |  O  | O  |  -        |
| 회원 CSV 다운로드             |  O    |  O  | -  |  -        |
| 환불계좌 전체 보기            |  O    |  O  | O  |  -        |
| DB 접속 토큰 발급             |  O    |  O  | -  |  -        |
| 계정·권한 관리                |  O    |  -  | -  |  -        |

권한이 없으면 403 FORBIDDEN. 식별자를 먼저 적은 뒤 거부하므로, 접속기록 대상 라우트의 거부도
FAILURE로 남는다(Agent 미들웨어 — 응답 상태로 결과를 정함).
"""

from collections.abc import Callable
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

ROLES = ("ADMIN", "OPS", "CS", "MARKETING")
# 기능 → 쓸 수 있는 역할 (모듈 설명의 표)
PERMISSIONS: dict[str, frozenset[str]] = {
    "MEMBERS": frozenset(ROLES),
    "ORDERS": frozenset({"ADMIN", "OPS", "CS"}),
    "INQUIRIES": frozenset({"ADMIN", "OPS", "CS"}),
    "MEMBER_EXPORT": frozenset({"ADMIN", "OPS"}),
    "REFUND_FULL_VIEW": frozenset({"ADMIN", "OPS", "CS"}),
    "DB_TOKEN": frozenset({"ADMIN", "OPS"}),
    "ACCOUNTS": frozenset({"ADMIN"}),
}


@dataclass(frozen=True)
class AuthenticatedOperator:
    id: int
    login_id: str
    name: str
    team: str
    role: str
    must_change_password: bool = False


def permissions_of(role: str) -> list[str]:
    """화면이 메뉴를 숨기는 데 쓰는 목록 — 허용 여부는 언제나 서버가 다시 판단한다"""
    return [name for name, roles in PERMISSIONS.items() if role in roles]


def is_blocked(employment_status: str, failed_login_count: int) -> bool:
    return employment_status != "ACTIVE" or failed_login_count >= MAX_FAILED_LOGINS


def _unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "login required")


def _authenticate(request: Request, *, allow_password_change: bool) -> AuthenticatedOperator:
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
        row["id"],
        row["login_id"],
        row["name"],
        row["team"],
        row["role"],
        row["must_change_password"],
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
    # 임시 비밀번호 계정은 비밀번호를 바꾸기 전까지 업무 화면을 쓸 수 없다 (v0.1 보강 L-2)
    if authenticated.must_change_password and not allow_password_change:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "change the temporary password first")
    return authenticated


def current_operator(request: Request) -> AuthenticatedOperator:
    return _authenticate(request, allow_password_change=False)


def pending_operator(request: Request) -> AuthenticatedOperator:
    """비밀번호 변경 전이어도 쓸 수 있는 라우트(내 정보·비밀번호 변경)용"""
    return _authenticate(request, allow_password_change=True)


CurrentOperator = Annotated[AuthenticatedOperator, Depends(current_operator)]
PendingOperator = Annotated[AuthenticatedOperator, Depends(pending_operator)]


def require(permission: str) -> Callable[..., AuthenticatedOperator]:
    """역할 검사 — 표에 없는 역할이면 403 (모듈 설명)"""
    roles = PERMISSIONS[permission]

    def dependency(operator_: CurrentOperator) -> AuthenticatedOperator:
        if operator_.role not in roles:
            raise ApiError(403, "FORBIDDEN", f"{permission} is not allowed for {operator_.role}")
        return operator_

    return dependency


MembersOperator = Annotated[AuthenticatedOperator, Depends(require("MEMBERS"))]
OrdersOperator = Annotated[AuthenticatedOperator, Depends(require("ORDERS"))]
InquiriesOperator = Annotated[AuthenticatedOperator, Depends(require("INQUIRIES"))]
ExportOperator = Annotated[AuthenticatedOperator, Depends(require("MEMBER_EXPORT"))]
RefundViewOperator = Annotated[AuthenticatedOperator, Depends(require("REFUND_FULL_VIEW"))]
DbTokenOperator = Annotated[AuthenticatedOperator, Depends(require("DB_TOKEN"))]
AccountsOperator = Annotated[AuthenticatedOperator, Depends(require("ACCOUNTS"))]


def optional_operator(request: Request) -> AuthenticatedOperator | None:
    """인증돼 있으면 그 취급자, 아니면 None — 비인증 요청도 받아야 하는 라우트(로그아웃)용.
    인증된 경우에만 식별자를 기록지에 적으므로, 비인증 요청은 기록되지 않는다.
    임시 비밀번호 계정도 로그아웃은 할 수 있다."""
    try:
        return pending_operator(request)
    except ApiError:
        return None


OptionalOperator = Annotated[AuthenticatedOperator | None, Depends(optional_operator)]
