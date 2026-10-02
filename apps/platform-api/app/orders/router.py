"""주문·결제 조회 — 관리자 (PLT-16) — 감시 대상

목록에는 결제수단 정보가 카드사 이름뿐이다(카드번호는 저장하지 않음) → 데이터 유형 ORDER.
처리한 정보주체 = 화면에 표시된 주문의 회원(탈퇴로 끊긴 주문은 정보주체 없음).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.agent import access_log, record_subjects
from app.auth.deps import CurrentOperator
from app.models import member, orders, payment, product

router = APIRouter(prefix="/admin/orders")


@router.get("")
@access_log(action="READ", data_category="ORDER")
def list_orders(
    request: Request,
    _operator: CurrentOperator,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(orders)).scalar_one()
        rows = conn.execute(
            select(
                orders.c.id,
                orders.c.member_id,
                member.c.name.label("member_name"),
                product.c.name.label("product_name"),
                orders.c.amount,
                orders.c.status,
                orders.c.ordered_at,
                payment.c.card_company,
                payment.c.pg_tid,
            )
            .select_from(
                orders.join(product, product.c.id == orders.c.product_id)
                .outerjoin(member, member.c.id == orders.c.member_id)
                .outerjoin(payment, payment.c.order_id == orders.c.id)
            )
            .order_by(orders.c.ordered_at.desc(), orders.c.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        ).mappings()
        items = [dict(r) for r in rows]
    # 같은 회원의 주문이 여럿이어도 정보주체는 한 번만
    record_subjects(dict.fromkeys(i["member_id"] for i in items if i["member_id"] is not None))
    return {"items": items, "page": page, "size": size, "total": total}
