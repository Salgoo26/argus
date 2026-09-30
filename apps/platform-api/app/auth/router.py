"""관리자 로그인·로그아웃 (PLT-02, PLT-10)

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

from app.auth.deps import MAX_FAILED_LOGINS
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
def logout(request: Request, response: Response) -> None:
    clear_session_cookie(response, request.app.state.settings)
