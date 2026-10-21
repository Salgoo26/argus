"""상품·주문·결제 — 고객 (PLT-04, PLT-05, 기능 레이어 7-4 ③, 커머스 확장 F6)

결제는 가상 PG 서비스(apps/mock-pg)로 한다 — 실제 쇼핑몰의 PG 연동과 같은 흐름:
1. POST /shop/orders: 주문을 결제대기(PENDING_PAYMENT)로 만들고, 화면이 PG 결제창(작은 창)에
   POST할 값(주문번호·주문명·금액·구매자 이름·이메일·휴대폰)을 돌려준다
2. 고객은 PG 결제창에서 카드사·카드번호를 입력한다
   — **카드번호는 PG만 받는다**(이 서버를 거치지 않음)
3. 결제창이 쇼핑몰 화면으로 paymentKey를 돌려주면, POST /shop/orders/{id}/payment가 PG 승인 API를
   부르고(금액 대조) 승인 결과(카드사·마스킹 카드번호·승인번호·할부·영수증 주소)를 저장 → PAID
그래서 이 API에는 **카드번호를 받는 칸 자체가 없다** — 어디에도 남지 않는다(tests/test_checkout.py).

주문은 로그인 필수, 등록된 배송지 중 하나를 고른다(7-4 ③). 고른 배송지는 주문 행에 **복사**한다 —
배송지를 고치거나 지워도 주문 기록은 그대로(db-schema 4절 "플랫폼 보강").
배송 메모도 주문할 때 받는다.
고객 본인 행위라 접속기록 대상이 아니다 (CLAUDE.md 3절 #4).

배송 조회·취소·청약철회(커머스 확장, app/fulfillment.py): 내 주문의 배송 상태·예상 도착일·
택배사·송장번호를 보여 주고, 상품준비중까지는 취소, 배송완료 후 7일 이내에는 청약철회를 받는다.
결제하지 않은 주문(결제대기·만료)은 내역에 보이지 않는다.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select

from app.errors import ApiError
from app.fulfillment import (
    CANCEL_REASONS,
    CUSTOMER_VISIBLE,
    RETURN_REASONS,
    cancel_order,
    claims_of,
    confirm_payment,
    expected_delivery,
    expire_stale_payments,
    lock_order,
    payment_request,
    request_return,
    withdrawal_deadline,
)
from app.models import order_status_history, orders, payment, product, shipping_address
from app.shop.deps import CurrentMember

router = APIRouter(prefix="/shop")


class OrderRequest(BaseModel):
    product_id: Annotated[int, Field(gt=0)]
    shipping_address_id: Annotated[int, Field(gt=0)]
    # 배송 메모 — 택배 접수 때 함께 보낸다 (F6 Q2)
    delivery_memo: Annotated[str, Field(max_length=50)] | None = None


class PaymentConfirm(BaseModel):
    # PG 결제창이 돌려준 값 — 금액은 서버가 주문 금액과 대조한 뒤 PG 승인 API에 다시 확인받는다
    payment_key: Annotated[str, Field(min_length=1, max_length=40)]
    amount: Annotated[int, Field(gt=0)]


class CancelRequest(BaseModel):
    reason_code: Literal[CANCEL_REASONS]  # type: ignore[valid-type]
    reason_detail: Annotated[str, Field(max_length=500)] | None = None


class ReturnRequest(BaseModel):
    reason_code: Literal[RETURN_REASONS]  # type: ignore[valid-type]
    reason_detail: Annotated[str, Field(max_length=500)] | None = None


CLAIM_FIELDS = (
    "claim_type",
    "reason_code",
    "reason_detail",
    "status",
    "requested_at",
    "completed_at",
    "refund_amount",
    "pickup_status",
    "pickup_tracking_no",
)


def _order_rows(conn, member_id: int, order_id: int | None = None) -> list[dict]:
    query = (
        select(
            orders.c.id,
            product.c.name.label("product_name"),
            product.c.ship_days,
            orders.c.amount,
            orders.c.status,
            orders.c.ordered_at,
            orders.c.courier,
            orders.c.courier_status,
            orders.c.tracking_no,
            orders.c.shipped_at,
            orders.c.delivered_at,
            orders.c.ship_recipient,
            orders.c.ship_phone,
            orders.c.ship_zip_code,
            orders.c.ship_address,
            orders.c.ship_address_detail,
            orders.c.delivery_memo,
            payment.c.card_company,
            payment.c.card_number_masked,
            payment.c.installment_months,
            payment.c.approve_no,
            payment.c.receipt_url,
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
    )
    if order_id is None:
        query = query.where(orders.c.status.in_(CUSTOMER_VISIBLE))
    else:
        query = query.where(orders.c.id == order_id)
    items = [dict(r) for r in conn.execute(query).mappings()]
    claims = claims_of(conn, [i["id"] for i in items])
    for item in items:
        item["expected_delivery_at"] = expected_delivery(item["ordered_at"], item.pop("ship_days"))
        item["return_deadline"] = withdrawal_deadline(item["delivered_at"])
        item["claims"] = [{k: c[k] for k in CLAIM_FIELDS} for c in claims.get(item["id"], [])]
    return items


@router.get("/products")
def list_products(request: Request) -> dict:
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(select(product).order_by(product.c.id)).mappings()
        items = [dict(r) for r in rows]
    return {"items": items}


@router.post("/orders", status_code=201)
def create_order(body: OrderRequest, request: Request, me: CurrentMember) -> dict:
    """주문 생성(결제대기) → PG 결제창에 보낼 값. 결제는 /shop/orders/{id}/payment에서 끝난다"""
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
        expire_stale_payments(conn, now, me.id)
        # 금액은 서버가 상품 가격으로 정한다(화면이 보낸 금액을 믿지 않음)
        order = (
            conn.execute(
                insert(orders)
                .values(
                    member_id=me.id,
                    product_id=item["id"],
                    amount=item["price"],
                    status="PENDING_PAYMENT",
                    ordered_at=now,
                    ship_recipient=address["recipient"],
                    ship_phone=address["phone"],
                    ship_zip_code=address["zip_code"],
                    ship_address=address["address"],
                    ship_address_detail=address["address_detail"],
                    delivery_memo=(body.delivery_memo or "").strip() or None,
                )
                .returning(*orders.c)
            )
            .mappings()
            .one()
        )
        conn.execute(
            insert(order_status_history).values(
                order_id=order["id"],
                to_status="PENDING_PAYMENT",
                actor_type="CUSTOMER",
                changed_at=now,
            )
        )
        return {
            "order": _order_rows(conn, me.id, order["id"])[0],
            "payment_request": payment_request(conn, order, request.app.state.partners),
        }


@router.post("/orders/{order_id}/payment")
def confirm_my_payment(order_id: int, body: PaymentConfirm, request: Request, me: CurrentMember):
    """PG 결제창에서 돌아온 paymentKey로 승인 요청 → 결제완료"""
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = lock_order(conn, order_id, me.id)
        result = confirm_payment(
            conn, order, now, request.app.state.partners, body.payment_key, body.amount
        )
    if result == "EXPIRED":
        # 만료 상태는 위 트랜잭션에서 커밋한 뒤 오류로 알린다
        raise ApiError(409, "PAYMENT_EXPIRED", "payment time (30 minutes) has passed")
    with request.app.state.engine.connect() as conn:
        return _order_rows(conn, me.id, order_id)[0]


@router.get("/orders")
def my_orders(request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        expire_stale_payments(conn, now, me.id)
        return {"items": _order_rows(conn, me.id)}


def _my_order(conn, member_id: int, order_id: int) -> dict:
    return _order_rows(conn, member_id, order_id)[0]


@router.post("/orders/{order_id}/cancel")
def cancel_my_order(order_id: int, body: CancelRequest, request: Request, me: CurrentMember):
    """주문 취소 — 상품준비중까지. 택배 접수 취소 → PG 승인 취소 (판단기록 F3·F6)"""
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = lock_order(conn, order_id, me.id)
        cancel_order(
            conn, order, now, body.reason_code, body.reason_detail, request.app.state.partners
        )
        return _my_order(conn, me.id, order_id)


@router.post("/orders/{order_id}/return")
def return_my_order(order_id: int, body: ReturnRequest, request: Request, me: CurrentMember):
    """청약철회(반품) 신청 — 배송완료 후 7일 이내, 택배 회수 접수 (전자상거래법 제17조제1항)"""
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = lock_order(conn, order_id, me.id)
        request_return(
            conn, order, now, body.reason_code, body.reason_detail, request.app.state.partners
        )
        return _my_order(conn, me.id, order_id)
