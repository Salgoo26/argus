"""Argus 로그인·로그아웃 (policy 4-3 — 플랫폼 관리자 로그인과 같은 기준)

- 연속 5회 실패 → status LOCKED, 성공하면 실패 횟수 0. 해제는 app.scripts.users unlock
- 없는 ID·틀린 비밀번호는 같은 응답(401) + 더미 해시 검증으로 응답 시간도 같게 — 계정 열거 방지
- 잠김·비활성(403)은 비밀번호가 맞았을 때만 알려준다
- 자체 접속기록: 존재하는 계정의 시도만 LOGIN으로 기록(성공·실패), 없는 ID는 기록하지 않는다.
  로그인한 사용자의 로그아웃은 LOGOUT으로 기록 (v0.1 보강 A)
"""

from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, update

from app.agent import access_log, access_log_exempt, record_actor
from app.auth.deps import (
    MAX_FAILED_LOGINS,
    CurrentUser,
    OptionalUser,
    block_reason,
    user_query,
)
from app.auth.passwords import dummy_password_hash, verify_password
from app.auth.tokens import clear_session_cookie, issue_token, set_session_cookie
from app.config import Settings
from app.errors import ApiError
from app.models import argus_user, push_subscription

router = APIRouter(prefix="/api/auth")

_BLOCK_MESSAGES = {
    "ACCOUNT_DISABLED": "account is disabled",
    "ACCOUNT_LOCKED": "account is locked",
}


class LoginRequest(BaseModel):
    login_id: Annotated[str, Field(min_length=1, max_length=64)]
    # 상한: 초대형 비밀번호로 해시 계산 시간을 늘리는 공격 방지
    password: Annotated[str, Field(min_length=1, max_length=256)]


def _invalid_credentials() -> ApiError:
    return ApiError(401, "INVALID_CREDENTIALS", "invalid login id or password")


def _profile(login_id: str, role: str, handler_name: str | None) -> dict:
    return {"login_id": login_id, "role": role, "name": handler_name}


@router.post("/login")
@access_log(action="LOGIN", data_category="NONE")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    settings: Settings = request.app.state.settings

    # 결과를 트랜잭션 안에서 정하고 오류는 커밋 뒤에 던진다 — 실패 횟수 증가가 롤백되지 않게
    with request.app.state.engine.begin() as conn:
        row = (
            conn.execute(user_query().where(argus_user.c.login_id == body.login_id))
            .mappings()
            .first()
        )
        if row is not None:
            record_actor(row["login_id"])  # 존재하는 계정의 시도만 기록 (policy 4-3)

        if row is None:
            verify_password(dummy_password_hash(), body.password)  # 응답 시간 균일화
            error = _invalid_credentials()
        elif not verify_password(row["password_hash"], body.password):
            failed = argus_user.c.failed_login_count + 1
            conn.execute(
                update(argus_user)
                .where(argus_user.c.id == row["id"])
                .values(
                    # 증가와 잠금을 한 문장으로 — 동시 시도가 서로의 증가분을 덮어쓰지 않게
                    failed_login_count=failed,
                    status=case(
                        (
                            (argus_user.c.status == "ACTIVE") & (failed >= MAX_FAILED_LOGINS),
                            "LOCKED",
                        ),
                        else_=argus_user.c.status,
                    ),
                )
            )
            error = _invalid_credentials()
        elif (reason := block_reason(row)) is not None:
            error = ApiError(403, reason, _BLOCK_MESSAGES[reason])
        else:
            conn.execute(
                update(argus_user)
                .where(argus_user.c.id == row["id"])
                .values(failed_login_count=0, last_login_at=func.now())
            )
            error = None

    if error is not None:
        raise error

    token = issue_token(row["id"], request.app.state.auth_secret, settings.session_idle_minutes)
    set_session_cookie(response, token, settings)
    return _profile(row["login_id"], row["role"], row["handler_name"])


@router.post("/logout", status_code=204)
@access_log(action="LOGOUT", data_category="NONE")
def logout(request: Request, response: Response, _user: OptionalUser) -> None:
    """로그아웃도 자체 접속기록 (v0.1 보강 A — 안내서 FAQ 147). 쿠키가 없거나 만료된 요청은
    행위자가 없어 기록하지 않고, 응답은 똑같이 쿠키를 지운다."""
    # 행위자를 확인하느라 재발급한 세션을 응답에 싣지 않는다 (main.py 쿠키 연장 미들웨어)
    request.state.session_token = None
    if _user is not None:
        # 로그아웃하면 웹 푸시도 끊는다 — 공용 PC에 남은 브라우저로 알림이 가지 않게 (F-4)
        with request.app.state.engine.begin() as conn:
            conn.execute(delete(push_subscription).where(push_subscription.c.user_id == _user.id))
    clear_session_cookie(response, request.app.state.settings)


@router.get("/me")
@access_log_exempt("본인 계정 정보만 반환 — 정보주체 처리 없음")
def me(user: CurrentUser) -> dict:
    return _profile(user.login_id, user.role, user.handler_name)
