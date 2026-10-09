"""관리자 계정·권한 관리 (v0.1 보강 L-2 — 고시 §5①③, 안내서 61~62) — ADMIN 전용

- GET  /admin/accounts                       계정 목록 (아이디·이름·팀·역할·재직 상태·최근 변경)
- POST /admin/accounts                       부여: 새 계정 → 임시 비밀번호를 **이 응답에서 한 번만**
- POST /admin/accounts/{id}/change           변경: 역할·팀
- POST /admin/accounts/{id}/terminate        말소: 퇴직 처리 — 이후 로그인·DB 토큰 발급 불가
- POST /admin/accounts/history/search        권한 이력 — 기간·대상 (조건은 본문)

규칙
- **모든 변경에 사유 필수**(안내서 62 — 사유 없는 권한 내역은 미흡 사례)
- **본인 계정은 바꿀 수 없다**(자기 권한 상승·자기 말소 방지)
- 변경마다 operator_permission_history(append-only)에 변경 전·후·사유·처리자를 남기고,
  Argus 취급자 명부 동기화 이벤트(HANDLER_CREATED/UPDATED/TERMINATED)를 outbox에 같은 트랜잭션으로
  넣는다 — 역할(role)은 지금처럼 보내지 않는다(outbox.handler_event)

접속기록: 회원 개인정보 처리가 아니므로 Agent 기록 대상이 아니다. 대신 권한 이력이 증적이다
(Argus 룰 변경 이력 detection_rule_history와 같은 방식)
"""

import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import Connection, func, insert, select, update

from app.agent import access_log_exempt
from app.auth.deps import ROLES, AccountsOperator, AuthenticatedOperator
from app.auth.passwords import hash_password
from app.errors import ApiError
from app.models import operator, operator_permission_history
from app.outbox import enqueue, handler_event

router = APIRouter(prefix="/admin/accounts")

KST = timezone(timedelta(hours=9))
_EXEMPT = "권한 이력(operator_permission_history)에 기록 — 회원 개인정보 처리 아님"
TEAMS = ("OPS", "CS", "MARKETING")

LoginId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{2,63}$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Role = Literal[ROLES]
Team = Literal[TEAMS]


def temporary_password() -> str:
    """임시 비밀번호 — 영문·숫자 섞인 16자. 저장은 해시만, 화면에 한 번만 보인다"""
    return secrets.token_urlsafe(12)[:14] + "a1"


def _view(row) -> dict:
    return {
        "id": row["id"],
        "login_id": row["login_id"],
        "name": row["name"],
        "team": row["team"],
        "role": row["role"],
        "employment_status": row["employment_status"],
        "terminated_at": row["terminated_at"],
        "must_change_password": row["must_change_password"],
        "last_changed_at": row["last_changed_at"],
    }


_last_changed = (
    select(func.max(operator_permission_history.c.created_at))
    .where(operator_permission_history.c.operator_id == operator.c.id)
    .scalar_subquery()
    .label("last_changed_at")
)


def _account_query():
    return select(
        operator.c.id,
        operator.c.login_id,
        operator.c.name,
        operator.c.team,
        operator.c.role,
        operator.c.employment_status,
        operator.c.terminated_at,
        operator.c.must_change_password,
        _last_changed,
    )


@router.get("")
@access_log_exempt(_EXEMPT)
def list_accounts(request: Request, _admin: AccountsOperator) -> dict:
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(_account_query().order_by(operator.c.id)).mappings().all()
    return {"items": [_view(r) for r in rows]}


def _record(
    conn: Connection,
    change_type: str,
    before,
    after,
    reason: str,
    actor: AuthenticatedOperator,
) -> None:
    conn.execute(
        insert(operator_permission_history).values(
            operator_id=after.id,
            change_type=change_type,
            before_role=before.role if before else None,
            after_role=after.role,
            before_team=before.team if before else None,
            after_team=after.team,
            before_status=before.employment_status if before else None,
            after_status=after.employment_status,
            reason=reason,
            actor_id=actor.id,
        )
    )


class CreateBody(BaseModel):
    login_id: LoginId
    name: Name
    team: Team
    role: Role
    reason: Reason


@router.post("", status_code=201)
@access_log_exempt(_EXEMPT)
def create_account(body: CreateBody, request: Request, admin: AccountsOperator) -> dict:
    password = temporary_password()
    with request.app.state.engine.begin() as conn:
        exists_ = conn.execute(
            select(operator.c.id).where(operator.c.login_id == body.login_id)
        ).first()
        if exists_ is not None:
            raise ApiError(409, "LOGIN_ID_TAKEN", "login id already exists")
        row = conn.execute(
            insert(operator)
            .values(
                login_id=body.login_id,
                password_hash=hash_password(password),
                name=body.name,
                team=body.team,
                role=body.role,
                must_change_password=True,
            )
            .returning(operator)
        ).one()
        _record(conn, "GRANT", None, row, body.reason, admin)
        enqueue(conn, "HANDLER", handler_event("HANDLER_CREATED", row, row.created_at))
        view = conn.execute(_account_query().where(operator.c.id == row.id)).mappings().one()
    # 임시 비밀번호는 응답에 한 번만 — 서버에는 해시만 남는다
    return _view(view) | {"temporary_password": password}


def _target(conn: Connection, operator_id: int, admin: AuthenticatedOperator):
    if operator_id == admin.id:
        raise ApiError(403, "SELF_CHANGE_FORBIDDEN", "you cannot change your own account")
    row = conn.execute(
        select(operator).where(operator.c.id == operator_id).with_for_update()
    ).first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "account not found")
    if row.employment_status != "ACTIVE":
        raise ApiError(409, "ACCOUNT_TERMINATED", "account is already terminated")
    return row


class ChangeBody(BaseModel):
    role: Role | None = None
    team: Team | None = None
    reason: Reason

    @model_validator(mode="after")
    def _something(self):
        if self.role is None and self.team is None:
            raise ValueError("role or team is required")
        return self


@router.post("/{operator_id}/change")
@access_log_exempt(_EXEMPT)
def change_account(
    operator_id: int, body: ChangeBody, request: Request, admin: AccountsOperator
) -> dict:
    with request.app.state.engine.begin() as conn:
        before = _target(conn, operator_id, admin)
        values = {k: v for k, v in (("role", body.role), ("team", body.team)) if v is not None}
        if all(getattr(before, k) == v for k, v in values.items()):
            raise ApiError(409, "NO_CHANGE", "nothing to change")
        after = conn.execute(
            update(operator)
            .where(operator.c.id == operator_id)
            .values(**values, updated_at=func.now())
            .returning(operator)
        ).one()
        _record(conn, "CHANGE", before, after, body.reason, admin)
        enqueue(conn, "HANDLER", handler_event("HANDLER_UPDATED", after, after.updated_at))
        view = conn.execute(_account_query().where(operator.c.id == operator_id)).mappings().one()
    return _view(view)


class TerminateBody(BaseModel):
    reason: Reason


@router.post("/{operator_id}/terminate")
@access_log_exempt(_EXEMPT)
def terminate_account(
    operator_id: int, body: TerminateBody, request: Request, admin: AccountsOperator
) -> dict:
    with request.app.state.engine.begin() as conn:
        before = _target(conn, operator_id, admin)
        after = conn.execute(
            update(operator)
            .where(operator.c.id == operator_id)
            .values(employment_status="TERMINATED", terminated_at=func.now(), updated_at=func.now())
            .returning(operator)
        ).one()
        _record(conn, "REVOKE", before, after, body.reason, admin)
        enqueue(conn, "HANDLER", handler_event("HANDLER_TERMINATED", after, after.terminated_at))
        view = conn.execute(_account_query().where(operator.c.id == operator_id)).mappings().one()
    return _view(view)


class HistorySearch(BaseModel):
    """권한 이력 검색 — 대상 아이디는 직원 개인정보라 URL이 아니라 본문으로"""

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
def search_history(body: HistorySearch, request: Request, _admin: AccountsOperator) -> dict:
    h = operator_permission_history.c
    target, actor = operator.alias("target"), operator.alias("actor")
    query = (
        select(
            h.id,
            target.c.login_id.label("login_id"),
            target.c.name.label("name"),
            h.change_type,
            h.before_role,
            h.after_role,
            h.before_team,
            h.after_team,
            h.before_status,
            h.after_status,
            h.reason,
            actor.c.login_id.label("actor_login_id"),  # NULL = 시스템
            h.created_at,
        )
        .join(target, target.c.id == h.operator_id)
        .outerjoin(actor, actor.c.id == h.actor_id)
    )
    if body.login_id:
        query = query.where(target.c.login_id == body.login_id)
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
        items = [dict(r) for r in rows]
    return {"items": items, "page": body.page, "size": body.size, "total": total}
