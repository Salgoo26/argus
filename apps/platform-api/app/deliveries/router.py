"""배송·반품 관리 — 관리자 (커머스 확장, 판단기록 F3·F4) — 감시 대상

- 역할: ADMIN·OPS·CS (주문 화면과 같은 ORDERS 권한, MARKETING 제외 — 배송 업무와 무관)
- 목록·검색: 받는 사람·연락처는 마스킹, 주소는 앞부분만. 전체 배송 정보는 주문 상세
  (/admin/orders/{id})에서 본다 — 목록 한 화면에 수십 명의 전체 주소·연락처가 한꺼번에 보이지 않게
- 검색 조건은 POST 본문, 접속기록에는 키 이름만 (app/admin_search.py)
- 배송 단계 넘기기·반품 회수 확인은 주문 정보 변경 → 접속기록 UPDATE·ORDER,
  정보주체 = 그 주문의 회원
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field, model_validator
from sqlalchemy import func, select

from app.admin_search import SearchPage, SearchText, check_period, kst_period
from app.agent import access_log, record_subjects
from app.auth.deps import OrdersOperator
from app.fulfillment import (
    STATUSES,
    advance_delivery,
    complete_return,
    expected_delivery,
    lock_order,
)
from app.models import orders, product

router = APIRouter(prefix="/admin/deliveries")


def mask_name(value: str | None) -> str | None:
    """홍길동 → 홍*동, 김민 → 김*"""
    if not value:
        return value
    if len(value) <= 2:
        return value[0] + "*"
    return value[0] + "*" * (len(value) - 2) + value[-1]


def mask_phone(value: str | None) -> str | None:
    """010-1234-5678 → 010-****-5678"""
    if not value:
        return value
    parts = value.split("-")
    if len(parts) == 3:
        return f"{parts[0]}-{'*' * len(parts[1])}-{parts[2]}"
    return value[:3] + "*" * max(len(value) - 7, 0) + value[-4:]


def mask_address(value: str | None) -> str | None:
    """앞의 두 단어(시·도, 시·군·구)만"""
    if not value:
        return value
    return " ".join(value.split()[:2]) + " …"


def _list_query():
    return select(
        orders.c.id,
        orders.c.member_id,
        product.c.name.label("product_name"),
        product.c.ship_days,
        orders.c.status,
        orders.c.ordered_at,
        orders.c.ship_recipient,
        orders.c.ship_phone,
        orders.c.ship_address,
        orders.c.courier,
        orders.c.tracking_no,
        orders.c.shipped_at,
        orders.c.delivered_at,
    ).select_from(orders.join(product, product.c.id == orders.c.product_id))


def _masked(row) -> dict:
    item = dict(row)
    item["expected_delivery_at"] = expected_delivery(item["ordered_at"], item.pop("ship_days"))
    item["ship_recipient"] = mask_name(item["ship_recipient"])
    item["ship_phone"] = mask_phone(item["ship_phone"])
    item["ship_address"] = mask_address(item["ship_address"])
    return item


def _page(conn, conditions: list, page: int, size: int) -> dict:
    total = conn.execute(select(func.count()).select_from(orders).where(*conditions)).scalar_one()
    rows = conn.execute(
        _list_query()
        .where(*conditions)
        .order_by(orders.c.ordered_at.desc(), orders.c.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    ).mappings()
    items = [_masked(r) for r in rows]
    record_subjects(dict.fromkeys(i["member_id"] for i in items if i["member_id"] is not None))
    return {"items": items, "page": page, "size": size, "total": total}


@router.get("")
@access_log(action="READ", data_category="ORDER")
def list_deliveries(
    request: Request,
    _operator: OrdersOperator,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    with request.app.state.engine.connect() as conn:
        return _page(conn, [], page, size)


class DeliverySearch(SearchPage):
    order_id: Annotated[int, Field(ge=1)] | None = None
    member_id: Annotated[int, Field(ge=1)] | None = None
    recipient: SearchText | None = None  # 받는 사람 부분 일치
    tracking_no: SearchText | None = None
    status: Literal[STATUSES] | None = None  # type: ignore[valid-type]
    ordered_from: date | None = None
    ordered_to: date | None = None

    @model_validator(mode="after")
    def _period(self):
        check_period(self.ordered_from, self.ordered_to)
        return self


@router.post("/search")
@access_log(action="READ", data_category="ORDER")
def search_deliveries(body: DeliverySearch, request: Request, _operator: OrdersOperator) -> dict:
    body.record_keys()
    o = orders.c
    conditions = kst_period(o.ordered_at, body.ordered_from, body.ordered_to)
    for column, value in (
        (o.id, body.order_id),
        (o.member_id, body.member_id),
        (o.status, body.status),
        (o.tracking_no, body.tracking_no),
    ):
        if value is not None:
            conditions.append(column == value)
    if body.recipient is not None:
        conditions.append(o.ship_recipient.contains(body.recipient, autoescape=True))
    with request.app.state.engine.connect() as conn:
        return _page(conn, conditions, body.page, body.size)


def _changed(order, to_status: str) -> dict:
    return {"id": order["id"], "from_status": order["status"], "status": to_status}


@router.post("/{order_id}/advance")
@access_log(action="UPDATE", data_category="ORDER")
def advance(order_id: int, request: Request, operator_: OrdersOperator) -> dict:
    """다음 배송 단계로 — 결제완료 → 상품준비중 → 배송중(가상 송장 발급) → 배송완료"""
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = lock_order(conn, order_id)
        if order["member_id"] is not None:
            record_subjects([order["member_id"]])
        to_status = advance_delivery(conn, order, now, operator_.id)
    return _changed(order, to_status)


@router.post("/{order_id}/return-complete")
@access_log(action="UPDATE", data_category="ORDER")
def return_complete(order_id: int, request: Request, operator_: OrdersOperator) -> dict:
    """반품 회수 확인 → 반품완료 + 카드 승인 취소(가상 PG)"""
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = lock_order(conn, order_id)
        if order["member_id"] is not None:
            record_subjects([order["member_id"]])
        complete_return(conn, order, now, operator_.id)
    return _changed(order, "RETURNED")
