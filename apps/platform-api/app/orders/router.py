"""주문·결제 조회 — 관리자 (PLT-16) — 감시 대상

목록에는 결제수단 정보가 카드사 이름뿐이다(카드번호는 저장하지 않음) → 데이터 유형 ORDER.
상세에는 배송 정보(주문 시점 스냅샷)가 더해진다 — 역시 주문 조회 ORDER (db-schema 4절 플랫폼 보강).
처리한 정보주체 = 화면에 표시된 주문의 회원(탈퇴로 끊긴 주문은 정보주체 없음).
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field, model_validator
from sqlalchemy import func, select

from app.admin_search import SearchPage, check_period, kst_period
from app.agent import access_log, record_subjects
from app.auth.deps import OrdersOperator
from app.errors import ApiError
from app.models import SHIP_COLUMNS, member, orders, payment, product

router = APIRouter(prefix="/admin/orders")


def _list_query():
    """목록·검색 공통 — 결제수단 정보는 카드사 이름뿐"""
    return select(
        orders.c.id,
        orders.c.member_id,
        member.c.name.label("member_name"),
        product.c.name.label("product_name"),
        orders.c.amount,
        orders.c.status,
        orders.c.ordered_at,
        payment.c.card_company,
        payment.c.pg_tid,
    ).select_from(
        orders.join(product, product.c.id == orders.c.product_id)
        .outerjoin(member, member.c.id == orders.c.member_id)
        .outerjoin(payment, payment.c.order_id == orders.c.id)
    )


@router.get("")
@access_log(action="READ", data_category="ORDER")
def list_orders(
    request: Request,
    _operator: OrdersOperator,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(orders)).scalar_one()
        rows = conn.execute(
            _list_query()
            .order_by(orders.c.ordered_at.desc(), orders.c.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        ).mappings()
        items = [dict(r) for r in rows]
    # 같은 회원의 주문이 여럿이어도 정보주체는 한 번만
    record_subjects(dict.fromkeys(i["member_id"] for i in items if i["member_id"] is not None))
    return {"items": items, "page": page, "size": size, "total": total}


class OrderSearch(SearchPage):
    order_id: Annotated[int, Field(ge=1)] | None = None
    member_id: Annotated[int, Field(ge=1)] | None = None
    status: Literal["PAID"] | None = None  # 지금 주문 상태는 결제 완료뿐 (db-schema 4절)
    ordered_from: date | None = None  # 한국 날짜, 양 끝 포함
    ordered_to: date | None = None

    @model_validator(mode="after")
    def _period(self):
        check_period(self.ordered_from, self.ordered_to)
        return self


@router.post("/search")
@access_log(action="READ", data_category="ORDER")
def search_orders(body: OrderSearch, request: Request, _operator: OrdersOperator) -> dict:
    """주문 검색 (v0.1 보강 E) — 조건은 본문, 접속기록에는 키 이름만 (app/admin_search.py)"""
    body.record_keys()
    o = orders.c
    conditions = kst_period(o.ordered_at, body.ordered_from, body.ordered_to)
    for column, value in (
        (o.id, body.order_id),
        (o.member_id, body.member_id),
        (o.status, body.status),
    ):
        if value is not None:
            conditions.append(column == value)
    with request.app.state.engine.connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(orders).where(*conditions)
        ).scalar_one()
        rows = conn.execute(
            _list_query()
            .where(*conditions)
            .order_by(o.ordered_at.desc(), o.id.desc())
            .limit(body.size)
            .offset((body.page - 1) * body.size)
        ).mappings()
        items = [dict(r) for r in rows]
    record_subjects(dict.fromkeys(i["member_id"] for i in items if i["member_id"] is not None))
    return {"items": items, "page": body.page, "size": body.size, "total": total}


@router.get("/{order_id}")
@access_log(action="READ", data_category="ORDER")
def get_order(order_id: int, request: Request, _operator: OrdersOperator) -> dict:
    """주문 상세 — 배송 정보 포함 (운영팀 배송 업무, 7-4 ③). 주문 조회 접속기록 READ·ORDER

    배송 정보는 주문할 때 복사해 둔 값이다(회원의 현재 배송지가 아님). 탈퇴 회원의 주문은
    분리보관으로 옮겨져 비어 있다. 카드번호는 플랫폼에 없다 — 카드사·PG 거래번호뿐.
    처리한 정보주체 = 그 주문의 회원 (탈퇴로 끊긴 주문은 정보주체 없음).
    """
    with request.app.state.engine.connect() as conn:
        row = (
            conn.execute(
                select(
                    orders.c.id,
                    orders.c.member_id,
                    member.c.name.label("member_name"),
                    product.c.name.label("product_name"),
                    orders.c.amount,
                    orders.c.status,
                    orders.c.ordered_at,
                    *(orders.c[c] for c in SHIP_COLUMNS),
                    payment.c.method,
                    payment.c.card_company,
                    payment.c.pg_tid,
                    payment.c.approved_at,
                )
                .select_from(
                    orders.join(product, product.c.id == orders.c.product_id)
                    .outerjoin(member, member.c.id == orders.c.member_id)
                    .outerjoin(payment, payment.c.order_id == orders.c.id)
                )
                .where(orders.c.id == order_id)
            )
            .mappings()
            .first()
        )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "order not found")
    if row["member_id"] is not None:
        record_subjects([row["member_id"]])
    return dict(row)
