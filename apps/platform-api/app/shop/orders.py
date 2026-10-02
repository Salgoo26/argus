"""상품·주문·결제(PG 목업) — 고객 (PLT-04, PLT-05, 기능 레이어 7 결정 7)

결제는 흉내만 낸다. 실제 서비스라면 고객은 PG사 결제창에서 카드번호를 입력하고, 쇼핑몰은 PG사의
승인 결과(거래번호·카드사·금액)만 받는다. 그래서 이 API는 **카드번호를 받는 칸 자체가 없다** —
화면의 가상 결제창에서 고른 카드사 이름만 받고, 거래번호는 서버가 지어낸다
(가상 PG의 승인 응답 자리).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select

from app.commerce import CARD_COMPANIES, mock_pg_tid
from app.errors import ApiError
from app.models import orders, payment, product
from app.shop.deps import CurrentMember

router = APIRouter(prefix="/shop")


class OrderRequest(BaseModel):
    product_id: Annotated[int, Field(gt=0)]
    # 가상 결제창에서 고른 카드사 — 카드번호가 아니다
    card_company: Literal[CARD_COMPANIES]  # type: ignore[valid-type]


def _order_rows(conn, member_id: int) -> list[dict]:
    rows = conn.execute(
        select(
            orders.c.id,
            product.c.name.label("product_name"),
            orders.c.amount,
            orders.c.status,
            orders.c.ordered_at,
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
    return [dict(r) for r in rows]


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
        now = conn.execute(select(func.now())).scalar_one()
        order_id = conn.execute(
            insert(orders)
            .values(
                member_id=me.id,
                product_id=item["id"],
                amount=item["price"],
                status="PAID",
                ordered_at=now,
            )
            .returning(orders.c.id)
        ).scalar_one()
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
        created = next(r for r in _order_rows(conn, me.id) if r["id"] == order_id)
    return created


@router.get("/orders")
def my_orders(request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.connect() as conn:
        return {"items": _order_rows(conn, me.id)}
