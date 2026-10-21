"""상품·주문·결제(PG 목업) — 고객 (PLT-04, PLT-05, 기능 레이어 7 결정 7 · 7-4 ③)

결제는 흉내만 낸다. 실제 서비스라면 고객은 PG사 결제창에서 카드번호를 입력하고, 쇼핑몰은 PG사의
승인 결과(거래번호·카드사·금액)만 받는다. 이 데모의 가상 결제창도 카드번호를 입력받지만
**브라우저 밖으로 보내지 않는다**(2026-10-07 사용자 결정, 형식 검사도 없음 — 10-08).
그래서 이 API는 **카드번호를 받는 칸 자체가 없다** — 화면이 잘못 보내도 모델에 칸이 없어 버려지고
어디에도 남지 않는다(로그·DB·Argus — tests/test_checkout.py).
거래번호는 서버가 지어낸다(가상 PG의 승인 응답 자리).

주문은 로그인 필수, 등록된 배송지 중 하나를 고른다(7-4 ③). 고른 배송지는 주문 행에 **복사**한다 —
배송지를 고치거나 지워도 주문 기록은 그대로(db-schema 4절 "플랫폼 보강").
고객 본인 행위라 접속기록 대상이 아니다 (CLAUDE.md 3절 #4).

배송 조회·취소·청약철회(커머스 확장, app/fulfillment.py): 내 주문의 배송 상태·예상 도착일·
가상 택배사·송장번호를 보여 주고, 상품준비중까지는 취소, 배송완료 후 7일 이내에는 청약철회를 받는다.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select

from app.commerce import CARD_COMPANIES, mock_pg_tid
from app.errors import ApiError
from app.fulfillment import (
    CANCEL_REASONS,
    RETURN_REASONS,
    cancel_order,
    claims_of,
    expected_delivery,
    lock_order,
    request_return,
    withdrawal_deadline,
)
from app.models import order_status_history, orders, payment, product, shipping_address
from app.shop.deps import CurrentMember

router = APIRouter(prefix="/shop")


class OrderRequest(BaseModel):
    product_id: Annotated[int, Field(gt=0)]
    shipping_address_id: Annotated[int, Field(gt=0)]
    # 가상 결제창에서 고른 카드사 — 카드번호가 아니다
    card_company: Literal[CARD_COMPANIES]  # type: ignore[valid-type]


class CancelRequest(BaseModel):
    reason_code: Literal[CANCEL_REASONS]  # type: ignore[valid-type]
    reason_detail: Annotated[str, Field(max_length=500)] | None = None


class ReturnRequest(BaseModel):
    reason_code: Literal[RETURN_REASONS]  # type: ignore[valid-type]
    reason_detail: Annotated[str, Field(max_length=500)] | None = None


def _order_rows(conn, member_id: int) -> list[dict]:
    rows = conn.execute(
        select(
            orders.c.id,
            product.c.name.label("product_name"),
            product.c.ship_days,
            orders.c.amount,
            orders.c.status,
            orders.c.ordered_at,
            orders.c.courier,
            orders.c.tracking_no,
            orders.c.shipped_at,
            orders.c.delivered_at,
            orders.c.ship_recipient,
            orders.c.ship_phone,
            orders.c.ship_zip_code,
            orders.c.ship_address,
            orders.c.ship_address_detail,
            payment.c.card_company,
            payment.c.pg_tid,
            payment.c.approved_at,
        )
        .select_from(
            orders.join(product, product.c.id == orders.c.product_id).outerjoin(
                payment, payment.c.order_id == orders.c.id
            )
        )
        .where(orders.c.member_id == member_id)
        .order_by(orders.c.ordered_at.desc(), orders.c.id.desc())
    ).mappings()
    items = [dict(r) for r in rows]
    claims = claims_of(conn, [i["id"] for i in items])
    for item in items:
        item["expected_delivery_at"] = expected_delivery(item["ordered_at"], item.pop("ship_days"))
        item["return_deadline"] = withdrawal_deadline(item["delivered_at"])
        item["claims"] = [
            {
                k: c[k]
                for k in (
                    "claim_type",
                    "reason_code",
                    "reason_detail",
                    "status",
                    "requested_at",
                    "completed_at",
                    "refund_amount",
                )
            }
            for c in claims.get(item["id"], [])
        ]
    return items


@router.get("/products")
def list_products(request: Request) -> dict:
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(select(product).order_by(product.c.id)).mappings()
        items = [dict(r) for r in rows]
    return {"items": items, "card_companies": list(CARD_COMPANIES)}


@router.post("/orders", status_code=201)
def create_order(body: OrderRequest, request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.begin() as conn:
        item = conn.execute(select(product).where(product.c.id == body.product_id)).mappings()
        item = item.first()
        if item is None:
            raise ApiError(404, "NOT_FOUND", "product not found")
        # 내 배송지만 — 남의 배송지 번호로는 주문할 수 없다
        address = (
            conn.execute(
                select(shipping_address).where(
                    shipping_address.c.id == body.shipping_address_id,
                    shipping_address.c.member_id == me.id,
                )
            )
            .mappings()
            .first()
        )
        if address is None:
            raise ApiError(400, "SHIPPING_ADDRESS_REQUIRED", "choose one of your addresses")
        if not address["phone"] or not address["zip_code"]:
            # 예전 회원 주소에서 옮겨 온 배송지(0007)는 연락처·우편번호가 비어 있을 수 있다
            raise ApiError(400, "SHIPPING_ADDRESS_INCOMPLETE", "address needs phone and zip code")

        now = conn.execute(select(func.now())).scalar_one()
        order_id = conn.execute(
            insert(orders)
            .values(
                member_id=me.id,
                product_id=item["id"],
                amount=item["price"],
                status="PAID",
                ordered_at=now,
                ship_recipient=address["recipient"],
                ship_phone=address["phone"],
                ship_zip_code=address["zip_code"],
                ship_address=address["address"],
                ship_address_detail=address["address_detail"],
            )
            .returning(orders.c.id)
        ).scalar_one()
        conn.execute(
            insert(order_status_history).values(
                order_id=order_id, to_status="PAID", actor_type="CUSTOMER", changed_at=now
            )
        )
        # 가상 PG 승인 결과 — 금액은 서버가 상품 가격으로 정한다(화면이 보낸 금액을 믿지 않음)
        conn.execute(
            insert(payment).values(
                order_id=order_id,
                method="CARD",
                card_company=body.card_company,
                pg_tid=mock_pg_tid(),
                amount=item["price"],
                approved_at=now,
            )
        )
        created = _my_order(conn, me.id, order_id)
    return created


@router.get("/orders")
def my_orders(request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.connect() as conn:
        return {"items": _order_rows(conn, me.id)}


def _my_order(conn, member_id: int, order_id: int) -> dict:
    return next(r for r in _order_rows(conn, member_id) if r["id"] == order_id)


@router.post("/orders/{order_id}/cancel")
def cancel_my_order(order_id: int, body: CancelRequest, request: Request, me: CurrentMember):
    """주문 취소 — 상품준비중까지, 즉시 카드 승인 취소 (판단기록 F3)"""
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = lock_order(conn, order_id, me.id)
        cancel_order(conn, order, now, body.reason_code, body.reason_detail)
        return _my_order(conn, me.id, order_id)


@router.post("/orders/{order_id}/return")
def return_my_order(order_id: int, body: ReturnRequest, request: Request, me: CurrentMember):
    """청약철회(반품) 신청 — 배송완료 후 7일 이내 (전자상거래법 제17조제1항)"""
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = lock_order(conn, order_id, me.id)
        request_return(conn, order, now, body.reason_code, body.reason_detail)
        return _my_order(conn, me.id, order_id)
