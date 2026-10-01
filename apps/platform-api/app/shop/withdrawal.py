"""회원 탈퇴 = 즉시 파기 (기능 레이어 7 결정 6, PIPA §21①)

탈퇴 트랜잭션 안에서 회원 행과 그에 딸린 개인정보를 **실제 삭제**한다(논리 삭제 아님).
db-schema 4-1은 "상태 변경 → 파기 배치"였지만 파기 배치는 v0.2라, 그대로면 v0.1 내내 탈퇴 회원
정보가 남는다 → 탈퇴 시점에 바로 지운다(설계 변경).

순서 (db-schema 4-1 절차를 한 트랜잭션으로):
1. 법정 보존 대상만 retained_member_record로 분리보관 (PIPA §21③)
   - 대금결제·재화 공급 기록 5년 (전자상거래법 시행령 §6①3호) — 주문이 있을 때만
   - data에는 보존 목적에 필요한 최소 항목만: 주문번호·상품·금액·일시·PG 거래번호·카드사와
     분쟁 시 본인 확인용 이름·연락처. 비밀번호·주소·환불계좌는 담지 않는다
2. 회원을 참조하는 개인정보 삭제(환불계좌·동의 이력) → 회원 행 삭제.
   orders.member_id는 FK ON DELETE SET NULL로 끊겨 개인과 연결되지 않는 기록이 된다
3. destruction_history에 파기 이력 (개인정보 자체는 기록하지 않음)
"""

from datetime import timedelta

from sqlalchemy import Connection, delete, func, insert, select

from app.models import (
    destruction_history,
    member,
    member_consent,
    orders,
    payment,
    product,
    refund_account,
    retained_member_record,
)

PAYMENT_RETENTION = timedelta(days=365 * 5)
PAYMENT_LEGAL_BASIS = "전자상거래법 시행령 §6①3호"
DESTRUCTION_LEGAL_BASIS = "개인정보 보호법 §21①"


def _retain_orders(conn: Connection, profile, now) -> None:
    rows = conn.execute(
        select(
            orders.c.id,
            product.c.name,
            orders.c.amount,
            orders.c.ordered_at,
            payment.c.pg_tid,
            payment.c.card_company,
        )
        .select_from(
            orders.join(product, product.c.id == orders.c.product_id).outerjoin(
                payment, payment.c.order_id == orders.c.id
            )
        )
        .where(orders.c.member_id == profile["id"])
        .order_by(orders.c.id)
    ).all()
    if not rows:
        return
    conn.execute(
        insert(retained_member_record).values(
            original_member_id=profile["id"],
            retain_reason="PAYMENT_5Y",
            legal_basis=PAYMENT_LEGAL_BASIS,
            data={
                "contact": {
                    "name": profile["name"],
                    "email": profile["email"],
                    "phone": profile["phone"],
                },
                "orders": [
                    {
                        "order_id": r.id,
                        "product": r.name,
                        "amount": r.amount,
                        "ordered_at": r.ordered_at.isoformat(),
                        "pg_tid": r.pg_tid,
                        "card_company": r.card_company,
                    }
                    for r in rows
                ],
            },
            retain_until=now + PAYMENT_RETENTION,
            created_at=now,
        )
    )


def destroy_member(conn: Connection, member_id: int) -> None:
    now = conn.execute(select(func.now())).scalar_one()
    profile = (
        conn.execute(
            select(member.c.id, member.c.name, member.c.email, member.c.phone).where(
                member.c.id == member_id
            )
        )
        .mappings()
        .one()
    )
    _retain_orders(conn, profile, now)

    conn.execute(delete(refund_account).where(refund_account.c.member_id == member_id))
    # 동의 이력도 함께 파기 — 탈퇴로 동의 자체가 실효된다 (db-schema 4-1 "동의 이력" 주석)
    conn.execute(delete(member_consent).where(member_consent.c.member_id == member_id))
    conn.execute(delete(member).where(member.c.id == member_id))

    conn.execute(
        insert(destruction_history).values(
            executed_at=now,
            target_type="MEMBER",
            cutoff_at=now,
            deleted_count=1,
            legal_basis=DESTRUCTION_LEGAL_BASIS,
        )
    )
