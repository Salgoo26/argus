"""Argus 계정 관리 (v0.1 보강 L-4 — 고시 §5①③, 안내서 61~62) — 정보보호 담당자 전용

- GET  /api/users                      계정 목록 (아이디·역할·상태·명부 연결·최근 로그인)
- POST /api/users/{id}/promote         부여: 취급자 → 담당자
- POST /api/users/{id}/demote          변경: 담당자 → 취급자 (명부에 연결된 계정만)
- POST /api/users/{id}/disable         말소: 비활성화
- POST /api/users/{id}/enable          재활성화 (명부에서 퇴직한 계정은 불가)
- POST /api/users/{id}/unlock          잠금 해제 (로그인 5회 실패)
- POST /api/users/history/search       계정 이력 — 대상·기간 (조건은 본문)

규칙: 모든 변경에 사유 필수, 본인 계정은 바꿀 수 없다, 이력(argus_user_history)은 append-only.
비밀번호는 화면에서 다루지 않는다 — 지금처럼 운영 스크립트(app.scripts.users set-password).
접속기록: 정보주체 처리가 아니므로 Argus 자체 기록 제외 — 이 이력이 증적(룰 변경 이력과 같은 방식)
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import Connection, delete, func, select, update

from app.agent import access_log_exempt
from app.auth.deps import AuthenticatedUser, CurrentUser
from app.detection.rules import KST
from app.detections.transitions import HANDLER, OFFICER
from app.errors import ApiError
from app.models import argus_user, argus_user_history, handler, push_subscription
from app.users.history import record_user_change

router = APIRouter(prefix="/api/users")

_EXEMPT = "계정 이력(argus_user_history)에 기록 — 정보주체 처리 아님"

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
LoginId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[\x21-\x7e]{1,64}$")]


class ReasonBody(BaseModel):
    reason: Reason


def _officer_only(user: AuthenticatedUser) -> None:
    if user.role != OFFICER:
        raise ApiError(403, "FORBIDDEN", "account management is for officers only")


def _view(row) -> dict:
    return {
        "id": row["id"],
        "login_id": row["login_id"],
        "role": row["role"],
        "status": row["status"],
        "last_login_at": row["last_login_at"],
        "created_at": row["created_at"],
        # 플랫폼 취급자 명부 연결 — 퇴직 동기화로 함께 막힌다
        "handler": (
            {
                "login_id": row["handler_login_id"],
                "name": row["handler_name"],
                "employment_status": row["employment_status"],
            }
            if row["handler_id"] is not None
            else None
        ),
        "last_changed_at": row["last_changed_at"],
    }


def _query():
    last_changed = (
        select(func.max(argus_user_history.c.created_at))
        .where(argus_user_history.c.user_id == argus_user.c.id)
        .scalar_subquery()
    )
    return select(
        argus_user.c.id,
        argus_user.c.login_id,
        argus_user.c.role,
        argus_user.c.status,
        argus_user.c.handler_id,
        argus_user.c.last_login_at,
        argus_user.c.created_at,
        handler.c.login_id.label("handler_login_id"),
        handler.c.name.label("handler_name"),
        handler.c.employment_status,
        last_changed.label("last_changed_at"),
    ).outerjoin(handler, handler.c.id == argus_user.c.handler_id)


@router.get("")
@access_log_exempt(_EXEMPT)
def list_users(request: Request, user: CurrentUser) -> dict:
    _officer_only(user)
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(_query().order_by(argus_user.c.id)).mappings().all()
    return {"items": [_view(r) for r in rows]}


def _target(conn: Connection, user_id: int, me: AuthenticatedUser):
    if user_id == me.id:
        raise ApiError(403, "SELF_CHANGE_FORBIDDEN", "you cannot change your own account")
    row = conn.execute(
        select(
            argus_user.c.id,
            argus_user.c.login_id,
            argus_user.c.role,
            argus_user.c.status,
            argus_user.c.handler_id,
            handler.c.employment_status,
        )
        .outerjoin(handler, handler.c.id == argus_user.c.handler_id)
        .where(argus_user.c.id == user_id)
        .with_for_update(of=argus_user)
    ).first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "account not found")
    return row


def _change(
    request: Request,
    me: AuthenticatedUser,
    user_id: int,
    reason: str,
    change_type: str,
    check,
    values: dict,
) -> dict:
    _officer_only(me)
    with request.app.state.engine.begin() as conn:
        before = _target(conn, user_id, me)
        check(before)
        conn.execute(update(argus_user).where(argus_user.c.id == user_id).values(**values))
        record_user_change(
            conn,
            user=before,
            before=before,
            change_type=change_type,
            after_role=values.get("role", before.role),
            after_status=values.get("status", before.status),
            reason=reason,
            actor=me,
        )
        if values.get("status") == "DISABLED":
            # 막힌 계정에는 웹 푸시도 보내지 않는다 (v0.1 보강 F-4와 같게)
            conn.execute(delete(push_subscription).where(push_subscription.c.user_id == user_id))
        row = conn.execute(_query().where(argus_user.c.id == user_id)).mappings().one()
    return _view(row)


def _conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)


@router.post("/{user_id}/promote")
@access_log_exempt(_EXEMPT)
def promote(user_id: int, body: ReasonBody, request: Request, user: CurrentUser) -> dict:
    def check(before):
        if before.role != HANDLER:
            raise _conflict("NOT_A_HANDLER", "only handler accounts can be promoted")

    return _change(request, user, user_id, body.reason, "GRANT", check, {"role": OFFICER})


@router.post("/{user_id}/demote")
@access_log_exempt(_EXEMPT)
def demote(user_id: int, body: ReasonBody, request: Request, user: CurrentUser) -> dict:
    def check(before):
        if before.role != OFFICER:
            raise _conflict("NOT_AN_OFFICER", "only officer accounts can be demoted")
        if before.handler_id is None:
            # 취급자는 명부의 플랫폼 계정과 이어져야 한다(본인 탐지건 판정)
            raise _conflict("NO_HANDLER_LINK", "account is not linked to the handler roster")

    return _change(request, user, user_id, body.reason, "CHANGE", check, {"role": HANDLER})


@router.post("/{user_id}/disable")
@access_log_exempt(_EXEMPT)
def disable(user_id: int, body: ReasonBody, request: Request, user: CurrentUser) -> dict:
    def check(before):
        if before.status == "DISABLED":
            raise _conflict("ALREADY_DISABLED", "account is already disabled")

    return _change(request, user, user_id, body.reason, "REVOKE", check, {"status": "DISABLED"})


@router.post("/{user_id}/enable")
@access_log_exempt(_EXEMPT)
def enable(user_id: int, body: ReasonBody, request: Request, user: CurrentUser) -> dict:
    def check(before):
        if before.status != "DISABLED":
            raise _conflict("NOT_DISABLED", "account is not disabled")
        if before.handler_id is not None and before.employment_status != "ACTIVE":
            # 플랫폼에서 퇴직한 사람의 계정은 되살리지 않는다 — 명부가 재직으로 바뀐 뒤에
            raise _conflict("HANDLER_TERMINATED", "linked handler has left the company")

    values = {"status": "ACTIVE", "failed_login_count": 0}
    return _change(request, user, user_id, body.reason, "RESTORE", check, values)


@router.post("/{user_id}/unlock")
@access_log_exempt(_EXEMPT)
def unlock(user_id: int, body: ReasonBody, request: Request, user: CurrentUser) -> dict:
    def check(before):
        if before.status != "LOCKED":
            raise _conflict("NOT_LOCKED", "account is not locked")

    values = {"status": "ACTIVE", "failed_login_count": 0}
    return _change(request, user, user_id, body.reason, "UNLOCK", check, values)


class HistorySearch(BaseModel):
    login_id: LoginId | None = None
    date_from: date | None = None  # 한국 날짜, 양 끝 포함
    date_to: date | None = None
    page: Annotated[int, Field(ge=1)] = 1
    size: Annotated[int, Field(ge=1, le=100)] = 50

    @model_validator(mode="after")
    def _period(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


@router.post("/history/search")
@access_log_exempt(_EXEMPT)
def search_history(body: HistorySearch, request: Request, user: CurrentUser) -> dict:
    _officer_only(user)
    h = argus_user_history.c
    query = select(argus_user_history)
    if body.login_id:
        query = query.where(h.login_id == body.login_id)
    if body.date_from:
        query = query.where(h.created_at >= datetime.combine(body.date_from, time(), KST))
    if body.date_to:
        end = datetime.combine(body.date_to + timedelta(days=1), time(), KST)
        query = query.where(h.created_at < end)
    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = conn.execute(
            query.order_by(h.created_at.desc(), h.id.desc())
            .limit(body.size)
            .offset((body.page - 1) * body.size)
        ).mappings()
        items = [
            {
                "id": r["id"],
                "login_id": r["login_id"],
                "change_type": r["change_type"],
                "before_role": r["before_role"],
                "after_role": r["after_role"],
                "before_status": r["before_status"],
                "after_status": r["after_status"],
                "reason": r["reason"],
                "actor_login_id": r["actor_login_id"],  # None = 운영 스크립트·명부 동기화
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    return {"items": items, "page": body.page, "size": body.size, "total": total}
