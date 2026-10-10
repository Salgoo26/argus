"""관리자 로그인·로그아웃 (PLT-02, PLT-10) — 둘 다 접속기록 대상(LOGIN·LOGOUT)

로그인 실패 정책 (implementation-log 2026-09-29 설계 변경 3):
- 연속 5회 실패 → 잠금. 성공하면 0으로. 해제는 app.scripts.unlock_operator
- 없는 ID·틀린 비밀번호는 같은 응답(401 INVALID_CREDENTIALS) — 계정 열거 방지
- 잠김·퇴직은 비밀번호가 맞았을 때만 알려준다(403). 비밀번호를 모르는 사람에게
  "이 계정은 있고 잠겨 있다"를 알려주지 않기 위함
"""

from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.agent import access_log, access_log_exempt, record_actor
from app.auth.deps import MAX_FAILED_LOGINS, CurrentOperator, OptionalOperator
from app.auth.passwords import dummy_password_hash, verify_password
from app.auth.tokens import clear_session_cookie, issue_token, set_session_cookie
from app.config import Settings
from app.errors import ApiError
from app.models import operator

router = APIRouter(prefix="/admin/auth")


class LoginRequest(BaseModel):
    login_id: Annotated[str, Field(min_length=1, max_length=64)]
    # 상한: 수 MB짜리 비밀번호로 해시 계산 시간을 늘리는 공격 방지
    password: Annotated[str, Field(min_length=1, max_length=256)]


def _invalid_credentials() -> ApiError:
    return ApiError(401, "INVALID_CREDENTIALS", "invalid login id or password")


@router.post("/login")
@access_log(action="LOGIN", data_category="NONE")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    settings: Settings = request.app.state.settings

    # 결과를 트랜잭션 안에서 정하고 오류는 커밋 뒤에 던진다 — 예외로 빠져나가면
    # 실패 횟수 증가까지 롤백되기 때문
    with request.app.state.engine.begin() as conn:
        row = (
            conn.execute(select(operator).where(operator.c.login_id == body.login_id))
            .mappings()
            .first()
        )
        if row is not None:
            # 존재하는 계정의 시도만 접속기록(LOGIN)에 남는다 — 성공·실패 모두.
            # 없는 ID는 식별자가 아니고, ID 칸에 잘못 입력된 비밀번호가 append-only 원장에
            # 영구히 남을 수 있어 기록하지 않는다 (implementation-log 2026-09-29 설계 변경 3)
            record_actor(row["login_id"])

        if row is None:
            verify_password(dummy_password_hash(), body.password)  # 응답 시간 균일화
            error = _invalid_credentials()
        elif not verify_password(row["password_hash"], body.password):
            # 증가를 DB에서 원자적으로 (동시 요청이 서로의 증가분을 덮어쓰지 않게)
            conn.execute(
                update(operator)
                .where(operator.c.id == row["id"])
                .values(failed_login_count=operator.c.failed_login_count + 1)
            )
            error = _invalid_credentials()
        elif row["employment_status"] != "ACTIVE":
            error = ApiError(403, "ACCOUNT_DISABLED", "account is disabled")
        elif row["failed_login_count"] >= MAX_FAILED_LOGINS:
            error = ApiError(403, "ACCOUNT_LOCKED", "account is locked")
        else:
            conn.execute(
                update(operator).where(operator.c.id == row["id"]).values(failed_login_count=0)
            )
            error = None

    if error is not None:
        raise error

    token = issue_token(row["id"], request.app.state.auth_secret, settings.session_idle_minutes)
    set_session_cookie(response, token, settings)
    return {
        "login_id": row["login_id"],
        "name": row["name"],
        "team": row["team"],
        "role": row["role"],
    }


@router.post("/logout", status_code=204)
@access_log(action="LOGOUT", data_category="NONE")
def logout(request: Request, response: Response, _operator: OptionalOperator) -> None:
    """로그아웃도 접속기록 (v0.1 보강 A — 안내서 FAQ 147). 쿠키가 없거나 만료된 요청은
    행위자가 없어 기록하지 않고, 응답은 똑같이 쿠키를 지운다."""
    # 행위자를 확인하느라 재발급한 세션을 응답에 싣지 않는다 (main.py 쿠키 연장 미들웨어)
    request.state.session_cookie = None
    clear_session_cookie(response, request.app.state.settings)


@router.get("/me")
@access_log_exempt("본인 계정 정보만 반환 — 정보주체 처리 없음")
def me(operator: CurrentOperator) -> dict:
    """화면이 현재 로그인한 취급자를 확인하는 용도 (M5 platform-web)"""
    return {
        "login_id": operator.login_id,
        "name": operator.name,
        "team": operator.team,
        "role": operator.role,
    }
