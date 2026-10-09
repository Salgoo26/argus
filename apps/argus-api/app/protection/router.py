"""보호 대상 등록부 화면 API (v0.1 보강 N-1·N-4) — 정보보호 담당자 전용

- GET  /api/protection                    현황 + DB → 테이블 → 컬럼 트리
- POST /api/protection/columns            등록·변경 (구조 목록에 있는 컬럼만, 사유 필수)
- POST /api/protection/columns/{id}/release  해제 (삭제가 아니라 비활성, 사유 필수)
- GET  /api/protection/history            등록 이력

현황(대시보드 "보호 대상 현황"): 미분류 컬럼 수, 마지막 구조 목록 수신 시각, 2년 보관 대상 여부,
테이블별 최근 30일 DB 직접 접근 건수·최근 접근 시각(원장 context.tables 기준).
접속기록: 정보주체 처리가 아니므로 Argus 자체 기록 제외 — 변경은 등록 이력이 증적
(룰 변경과 같은 방식)
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, StringConstraints, model_validator
from sqlalchemy import Connection, func, insert, select, text, update

from app.agent import access_log_exempt
from app.auth.deps import AuthenticatedUser, CurrentUser
from app.detections.transitions import OFFICER
from app.errors import ApiError
from app.models import (
    argus_user,
    db_schema_column,
    db_schema_receipt,
    protected_column,
    protected_column_history,
    source_system,
)
from app.protection.registry import (
    DB,
    TWO_YEAR_ITEMS,
    registry_version,
    table_categories,
)

router = APIRouter(prefix="/api/protection")

_EXEMPT = "보호 대상 등록부 — 정보주체 처리 없음, 변경은 protected_column_history에 기록"
SOURCE = "PLATFORM"

Item = Literal[
    "NAME",
    "EMAIL",
    "PHONE",
    "ADDRESS",
    "BIRTH",
    "ACCOUNT",
    "CARD",
    "UNIQUE_ID",
    "SENSITIVE",
    "CREDENTIAL",
    "MEMBER_ID",
    "OTHER",
    "NOT_PERSONAL",
]
Category = Literal["MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY"]
Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]

# 최근 30일 DB 직접 접근 — 테이블 이름은 스키마 접두어(public.)를 떼고 센다
_DB_ACCESS_SQL = text(
    """
    SELECT regexp_replace(t, '^.*\\.', '') AS table_name, count(*) AS cnt,
           max(a.occurred_at) AS last_at
      FROM access_log a
      JOIN source_system s ON s.id = a.source_system_id,
           jsonb_array_elements_text(
               CASE WHEN jsonb_typeof(a.context -> 'tables') = 'array'
                    THEN a.context -> 'tables' ELSE '[]'::jsonb END) AS t
     WHERE s.code = :source AND a.access_path = 'DB'
       AND a.occurred_at >= now() - interval '30 days'
     GROUP BY 1
    """
)


def _officer_only(user: AuthenticatedUser) -> None:
    if user.role != OFFICER:
        raise ApiError(403, "FORBIDDEN", "the protection registry is for officers only")


def _source_id(conn: Connection) -> int:
    return conn.execute(
        select(source_system.c.id).where(source_system.c.code == SOURCE)
    ).scalar_one()


def _column_view(registered, schema_type: str | None, in_schema: bool) -> dict:
    if registered is None:
        status = "UNCLASSIFIED"
    elif not registered.active:
        status = "RELEASED" if in_schema else "MISSING"
    elif not in_schema:
        status = "MISSING"  # 등록했는데 DB에 없음(이름 변경·삭제)
    else:
        status = "NOT_PERSONAL" if registered.item == "NOT_PERSONAL" else "PERSONAL"
    return {
        "id": registered.id if registered is not None else None,
        "data_type": schema_type,
        "item": registered.item if registered is not None else None,
        "data_category": registered.data_category if registered is not None else None,
        "member_key": bool(registered.member_key) if registered is not None else False,
        "active": bool(registered.active) if registered is not None else False,
        "status": status,
    }


@router.get("")
@access_log_exempt(_EXEMPT)
def overview(request: Request, user: CurrentUser) -> dict:
    _officer_only(user)
    with request.app.state.engine.connect() as conn:
        source_id = _source_id(conn)
        schema = {
            (r.table_name, r.column_name): r
            for r in conn.execute(
                select(db_schema_column).where(
                    db_schema_column.c.source_system_id == source_id,
                    db_schema_column.c.db_name == DB,
                )
            )
        }
        registered = {
            (r.table_name, r.column_name): r
            for r in conn.execute(
                select(protected_column).where(
                    protected_column.c.source_system_id == source_id,
                    protected_column.c.db_name == DB,
                )
            )
        }
        receipt = conn.execute(
            select(db_schema_receipt.c.received_at)
            .where(db_schema_receipt.c.db_name == DB)
            .order_by(db_schema_receipt.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        access = {
            r.table_name: (r.cnt, r.last_at)
            for r in conn.execute(_DB_ACCESS_SQL, {"source": SOURCE})
        }
        version = registry_version(conn)

    active = [r for r in registered.values() if r.active]
    categories = table_categories(active)
    tables: dict[str, list[dict]] = {}
    for key in sorted(set(schema) | set(registered), key=lambda k: (k[0], _ordinal(schema, k))):
        table, column = key
        row = schema.get(key)
        view = _column_view(registered.get(key), row.data_type if row else None, row is not None)
        tables.setdefault(table, []).append({"column_name": column, **view})

    two_year = sorted({r.item for r in active if r.item in TWO_YEAR_ITEMS})
    return {
        "version": version,
        "database": DB,
        "summary": {
            "unclassified": sum(
                1 for cols in tables.values() for c in cols if c["status"] == "UNCLASSIFIED"
            ),
            "personal_columns": sum(1 for r in active if r.item != "NOT_PERSONAL"),
            "personal_tables": len(categories),
            "missing": sum(1 for cols in tables.values() for c in cols if c["status"] == "MISSING"),
            "last_schema_at": receipt,
            # 고시 §8① — 고유식별정보·민감정보를 처리하면 접속기록 최소 2년 보관 대상
            "two_year_retention": bool(two_year),
            "two_year_items": two_year,
        },
        "tables": [
            {
                "table_name": table,
                "data_category": categories.get(table),
                "db_access_30d": access.get(table, (0, None))[0],
                "last_db_access_at": access.get(table, (0, None))[1],
                "columns": columns,
            }
            for table, columns in tables.items()
        ],
    }


def _ordinal(schema: dict, key: tuple[str, str]) -> int:
    row = schema.get(key)
    return row.ordinal if row is not None else 10_000


class ColumnBody(BaseModel):
    table_name: Name
    column_name: Name
    item: Item
    data_category: Category | None = None
    member_key: bool = False
    reason: Reason

    @model_validator(mode="after")
    def _consistent(self):
        if (self.item == "NOT_PERSONAL") != (self.data_category is None):
            raise ValueError("data_category is required for personal items and only for them")
        if self.member_key and self.item != "MEMBER_ID":
            raise ValueError("member_key is only for MEMBER_ID columns")
        return self


def _snapshot(row) -> dict:
    return {
        "db_name": row.db_name,
        "table_name": row.table_name,
        "column_name": row.column_name,
        "item": row.item,
        "data_category": row.data_category,
        "member_key": row.member_key,
        "active": row.active,
    }


def _history(conn: Connection, row, change_type: str, reason: str, user) -> None:
    conn.execute(
        insert(protected_column_history).values(
            column_id=row.id,
            change_type=change_type,
            snapshot=_snapshot(row),
            reason=reason,
            changed_by=user.id,
        )
    )


@router.post("/columns")
@access_log_exempt(_EXEMPT)
def register_column(body: ColumnBody, request: Request, user: CurrentUser) -> dict:
    """등록·변경 — 구조 목록(게이트웨이가 보낸 것)에 있는 컬럼만 (오타 방지)"""
    _officer_only(user)
    values = {
        "item": body.item,
        "data_category": body.data_category,
        "member_key": body.member_key,
    }
    with request.app.state.engine.begin() as conn:
        source_id = _source_id(conn)
        in_schema = conn.execute(
            select(db_schema_column.c.column_name).where(
                db_schema_column.c.source_system_id == source_id,
                db_schema_column.c.db_name == DB,
                db_schema_column.c.table_name == body.table_name,
                db_schema_column.c.column_name == body.column_name,
            )
        ).first()
        if in_schema is None:
            raise ApiError(409, "UNKNOWN_COLUMN", "column is not in the received DB structure")
        key = (
            protected_column.c.source_system_id == source_id,
            protected_column.c.db_name == DB,
            protected_column.c.table_name == body.table_name,
            protected_column.c.column_name == body.column_name,
        )
        existing = conn.execute(select(protected_column).where(*key).with_for_update()).first()
        if existing is None:
            row = conn.execute(
                insert(protected_column)
                .values(
                    source_system_id=source_id,
                    db_name=DB,
                    table_name=body.table_name,
                    column_name=body.column_name,
                    updated_by=user.id,
                    **values,
                )
                .returning(protected_column)
            ).one()
            change_type = "REGISTER"
        else:
            same = existing.active and all(getattr(existing, k) == v for k, v in values.items())
            if same:
                raise ApiError(409, "NO_CHANGE", "nothing to change")
            change_type = "CHANGE" if existing.active else "REGISTER"
            row = conn.execute(
                update(protected_column)
                .where(protected_column.c.id == existing.id)
                .values(active=True, updated_by=user.id, updated_at=func.now(), **values)
                .returning(protected_column)
            ).one()
        _history(conn, row, change_type, body.reason, user)
        version = registry_version(conn)
    return {"id": row.id, "change_type": change_type, "version": version}


class ReleaseBody(BaseModel):
    reason: Reason


@router.post("/columns/{column_id}/release")
@access_log_exempt(_EXEMPT)
def release_column(column_id: int, body: ReleaseBody, request: Request, user: CurrentUser) -> dict:
    """해제 — 삭제하지 않고 비활성. 이후 그 컬럼은 다시 "미분류"로 보인다"""
    _officer_only(user)
    with request.app.state.engine.begin() as conn:
        existing = conn.execute(
            select(protected_column).where(protected_column.c.id == column_id).with_for_update()
        ).first()
        if existing is None:
            raise ApiError(404, "NOT_FOUND", "registered column not found")
        if not existing.active:
            raise ApiError(409, "ALREADY_RELEASED", "column is already released")
        row = conn.execute(
            update(protected_column)
            .where(protected_column.c.id == column_id)
            .values(active=False, updated_by=user.id, updated_at=func.now())
            .returning(protected_column)
        ).one()
        _history(conn, row, "RELEASE", body.reason, user)
        version = registry_version(conn)
    return {"id": column_id, "change_type": "RELEASE", "version": version}


@router.get("/history")
@access_log_exempt(_EXEMPT)
def history(
    request: Request,
    user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    _officer_only(user)
    size = 50
    h = protected_column_history.c
    with request.app.state.engine.connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(protected_column_history)
        ).scalar_one()
        rows = conn.execute(
            select(protected_column_history, argus_user.c.login_id.label("changed_by_login"))
            .outerjoin(argus_user, argus_user.c.id == h.changed_by)
            .order_by(h.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        ).mappings()
        items = [
            {
                "id": r["id"],
                "version": f"r{r['id']}",
                "change_type": r["change_type"],
                "snapshot": r["snapshot"],
                "reason": r["reason"],
                "changed_by": r["changed_by_login"],  # None = 시스템(초기 등록)
                "changed_at": r["changed_at"],
            }
            for r in rows
        ]
    return {"items": items, "page": page, "size": size, "total": total}
