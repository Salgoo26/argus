"""배송·반품 관리 — 관리자 (커머스 확장, 판단기록 F3·F4) — 감시 대상

- 역할: ADMIN·OPS·CS (주문 화면과 같은 ORDERS 권한, MARKETING 제외 — 배송 업무와 무관)
- 목록·검색: 받는 사람·연락처는 마스킹, 주소는 앞부분만. 전체 배송 정보는 주문 상세
  (/admin/orders/{id})에서 본다 — 목록 한 화면에 수십 명의 전체 주소·연락처가 한꺼번에 보이지 않게
- 검색 조건은 POST 본문, 접속기록에는 키 이름만 (app/admin_search.py)
- 상품 준비·택배 접수·배송 조회(택배사 상태 다시 가져오기)·반품 회수 확인은 주문 정보 변경
  → 접속기록 UPDATE·ORDER, 정보주체 = 그 주문의 회원
- 택배 접수 뒤의 배송중·배송완료는 택배사 통지로 바뀐다(관리자가 넘기지 않음 — F6).
  택배 접수 없이 배송중인 연동 이전 주문만 관리자가 배송완료로 넘긴다
- 목록에는 결제된 주문만(결제대기·만료 제외)
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
    FULFILLMENT_STATUSES,
    complete_legacy_delivery,
    complete_return,
    dispatch_order,
    expected_delivery,
    lock_order,
    prepare_order,
    sync_order,
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
        orders.c.courier_shipment_id,
        orders.c.courier_status,
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
    conditions = [orders.c.status.in_(FULFILLMENT_STATUSES), *conditions]
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
    status: Literal[FULFILLMENT_STATUSES] | None = None  # type: ignore[valid-type]
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


def _locked(conn, order_id: int):
    order = lock_order(conn, order_id)
    if order["member_id"] is not None:
        record_subjects([order["member_id"]])
    return order


def _now(conn):
    return conn.execute(select(func.now())).scalar_one()


@router.post("/{order_id}/advance")
@access_log(action="UPDATE", data_category="ORDER")
def advance(order_id: int, request: Request, operator_: OrdersOperator) -> dict:
    """관리자가 넘기는 단계 — 결제완료 → 상품준비중, (연동 이전 주문만) 배송중 → 배송완료"""
    with request.app.state.engine.begin() as conn:
        now = _now(conn)
        order = _locked(conn, order_id)
        if order["status"] == "SHIPPING":
            to_status = complete_legacy_delivery(conn, order, now, operator_.id)
        else:
            to_status = prepare_order(conn, order, now, operator_.id)
    return _changed(order, to_status)


@router.post("/{order_id}/dispatch")
@access_log(action="UPDATE", data_category="ORDER")
def dispatch(order_id: int, request: Request, _operator: OrdersOperator) -> dict:
    """택배 접수 — 받는 사람·배송 메모·상품·주문번호를 가상 택배 서비스로 (F6 Q2)"""
    with request.app.state.engine.begin() as conn:
        now = _now(conn)
        order = _locked(conn, order_id)
        shipment = dispatch_order(conn, order, now, request.app.state.partners)
    return {
        **_changed(order, order["status"]),
        "courier_shipment_id": shipment["shipmentId"],
        "courier_status": shipment["status"],
    }


@router.post("/{order_id}/sync")
@access_log(action="UPDATE", data_category="ORDER")
def sync(order_id: int, request: Request, _operator: OrdersOperator) -> dict:
    """배송 조회 — 택배 서비스에서 배송·회수 상태를 다시 가져와 반영 (통지를 놓쳤을 때)"""
    with request.app.state.engine.begin() as conn:
        now = _now(conn)
        order = _locked(conn, order_id)
        results = sync_order(conn, order, now, request.app.state.partners)
        status = conn.execute(select(orders.c.status).where(orders.c.id == order_id)).scalar_one()
    return {**_changed(order, status), "results": results}


@router.post("/{order_id}/return-complete")
@access_log(action="UPDATE", data_category="ORDER")
def return_complete(order_id: int, request: Request, operator_: OrdersOperator) -> dict:
    """반품 회수 확인 → 반품완료 + PG 승인 취소 (택배사가 판매자 도착을 알린 뒤)"""
    with request.app.state.engine.begin() as conn:
        now = _now(conn)
        order = _locked(conn, order_id)
        complete_return(conn, order, now, operator_.id, request.app.state.partners)
    return _changed(order, "RETURNED")
