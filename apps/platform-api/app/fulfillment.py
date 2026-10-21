"""배송·취소·청약철회 공통 — 주문 상태 전이 (커머스 확장, 판단기록 F3·F4)

상태 (db 0011):
    PAID ─(고객 취소)──────────────────────────────▶ CANCELLED
     └▶ PREPARING ─(고객 취소)─────────────────────▶ CANCELLED
          └▶ SHIPPING ─▶ DELIVERED ─(7일 이내 청약철회)─▶ RETURN_REQUESTED ─(회수 확인)─▶ RETURNED

- 배송 진행(PAID → PREPARING → SHIPPING → DELIVERED)은 관리자가 한 단계씩 넘긴다
  (F4 — 자동 진행 없음)
- 고객 취소는 상품준비중까지(F3). 발송 뒤에는 배송완료 후 청약철회로만
- 청약철회 기간: 배송완료(재화 공급)일부터 7일 (전자상거래법 제17조제1항)
- 환불은 카드 승인 취소(가상 PG). 환불계좌는 쓰지 않는다(F3 — 환불계좌 기능은 그대로 둔다)
- 상태가 바뀔 때마다 order_status_history 1행 — 계약·청약철회 기록 (전자상거래법 제6조)
- 여러 화면이 동시에 같은 주문을 바꾸지 않게 주문 행을 잠그고(FOR UPDATE) 현재 상태를 확인한다
"""

import secrets
from datetime import datetime, timedelta

from sqlalchemy import Connection, insert, select, update

from app.errors import ApiError
from app.models import order_claim, order_status_history, orders, payment

STATUSES = (
    "PAID",
    "PREPARING",
    "SHIPPING",
    "DELIVERED",
    "CANCELLED",
    "RETURN_REQUESTED",
    "RETURNED",
)
# 관리자가 넘기는 배송 단계
NEXT_DELIVERY_STATUS = {"PAID": "PREPARING", "PREPARING": "SHIPPING", "SHIPPING": "DELIVERED"}
CANCELLABLE = frozenset({"PAID", "PREPARING"})
WITHDRAWAL_PERIOD = timedelta(days=7)  # 전자상거래법 제17조제1항
# 탈퇴를 막는 진행 중 상태 — 배송·반품이 끝나지 않은 주문의 배송 정보를 지우면 업무가 끊긴다
IN_PROGRESS = frozenset({"PAID", "PREPARING", "SHIPPING", "RETURN_REQUESTED"})

MOCK_COURIER = "데모택배(가상)"
CANCEL_REASONS = ("CHANGE_OF_MIND", "WRONG_ORDER", "DELAY", "OTHER")
RETURN_REASONS = ("CHANGE_OF_MIND", "DEFECT", "WRONG_ITEM", "OTHER")


def mock_tracking_no() -> str:
    """가상 송장번호 — 실제라면 택배사가 접수 때 발급하는 값"""
    return "DEMO" + "".join(secrets.choice("0123456789") for _ in range(10))


def mock_pg_cancel_tid() -> str:
    """가상 PG 승인 취소 거래번호"""
    return "MOCKPG-CANCEL-" + secrets.token_hex(8).upper()


def expected_delivery(ordered_at: datetime, ship_days: int) -> datetime:
    return ordered_at + timedelta(days=ship_days)


def withdrawal_deadline(delivered_at: datetime | None) -> datetime | None:
    return delivered_at + WITHDRAWAL_PERIOD if delivered_at else None


def lock_order(conn: Connection, order_id: int, member_id: int | None = None):
    """주문 행을 잠그고 읽는다. member_id를 주면 그 회원의 주문만(남의 주문은 없는 것처럼 404)"""
    query = select(orders).where(orders.c.id == order_id)
    if member_id is not None:
        query = query.where(orders.c.member_id == member_id)
    row = conn.execute(query.with_for_update()).mappings().first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "order not found")
    return row


def change_status(
    conn: Connection,
    order,
    to_status: str,
    now: datetime,
    *,
    actor_type: str,
    operator_id: int | None = None,
    **values,
) -> None:
    conn.execute(
        update(orders).where(orders.c.id == order["id"]).values(status=to_status, **values)
    )
    conn.execute(
        insert(order_status_history).values(
            order_id=order["id"],
            from_status=order["status"],
            to_status=to_status,
            actor_type=actor_type,
            operator_id=operator_id,
            changed_at=now,
        )
    )


def advance_delivery(conn: Connection, order, now: datetime, operator_id: int) -> str:
    """관리자: 다음 배송 단계로. 발송 때 가상 택배사·송장번호, 도착 때 배송완료 시각"""
    to_status = NEXT_DELIVERY_STATUS.get(order["status"])
    if to_status is None:
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot advance from {order['status']}")
    values: dict = {}
    if to_status == "SHIPPING":
        values = {"courier": MOCK_COURIER, "tracking_no": mock_tracking_no(), "shipped_at": now}
    elif to_status == "DELIVERED":
        values = {"delivered_at": now}
    change_status(
        conn, order, to_status, now, actor_type="OPERATOR", operator_id=operator_id, **values
    )
    return to_status


def _paid_amount(conn: Connection, order_id: int) -> int | None:
    return conn.execute(select(payment.c.amount).where(payment.c.order_id == order_id)).scalar()


def cancel_order(conn: Connection, order, now: datetime, reason_code: str, detail: str | None):
    """고객: 상품준비중까지 취소 → 즉시 카드 승인 취소(가상 PG)"""
    if order["status"] not in CANCELLABLE:
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot cancel in {order['status']}")
    conn.execute(
        insert(order_claim).values(
            order_id=order["id"],
            claim_type="CANCEL",
            reason_code=reason_code,
            reason_detail=detail,
            status="COMPLETED",
            requested_at=now,
            completed_at=now,
            refund_amount=_paid_amount(conn, order["id"]),
            refund_pg_tid=mock_pg_cancel_tid(),
        )
    )
    change_status(conn, order, "CANCELLED", now, actor_type="CUSTOMER")


def request_return(conn: Connection, order, now: datetime, reason_code: str, detail: str | None):
    """고객: 배송완료 후 7일 이내 청약철회 신청 → 반품접수"""
    if order["status"] != "DELIVERED":
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot return in {order['status']}")
    deadline = withdrawal_deadline(order["delivered_at"])
    if deadline is None or now > deadline:
        raise ApiError(409, "WITHDRAWAL_PERIOD_EXPIRED", "return period (7 days) has passed")
    conn.execute(
        insert(order_claim).values(
            order_id=order["id"],
            claim_type="RETURN",
            reason_code=reason_code,
            reason_detail=detail,
            status="REQUESTED",
            requested_at=now,
        )
    )
    change_status(conn, order, "RETURN_REQUESTED", now, actor_type="CUSTOMER")


def complete_return(conn: Connection, order, now: datetime, operator_id: int) -> None:
    """관리자: 반품 상품 회수 확인 → 반품완료 + 카드 승인 취소(가상 PG)"""
    if order["status"] != "RETURN_REQUESTED":
        raise ApiError(409, "INVALID_ORDER_STATUS", f"no return requested ({order['status']})")
    conn.execute(
        update(order_claim)
        .where(
            order_claim.c.order_id == order["id"],
            order_claim.c.claim_type == "RETURN",
            order_claim.c.status == "REQUESTED",
        )
        .values(
            status="COMPLETED",
            completed_at=now,
            completed_by=operator_id,
            refund_amount=_paid_amount(conn, order["id"]),
            refund_pg_tid=mock_pg_cancel_tid(),
        )
    )
    change_status(conn, order, "RETURNED", now, actor_type="OPERATOR", operator_id=operator_id)


def claims_of(conn: Connection, order_ids: list[int]) -> dict[int, list[dict]]:
    if not order_ids:
        return {}
    rows = conn.execute(
        select(order_claim).where(order_claim.c.order_id.in_(order_ids)).order_by(order_claim.c.id)
    ).mappings()
    result: dict[int, list[dict]] = {}
    for r in rows:
        result.setdefault(r["order_id"], []).append(dict(r))
    return result


def history_of(conn: Connection, order_id: int) -> list[dict]:
    rows = conn.execute(
        select(order_status_history)
        .where(order_status_history.c.order_id == order_id)
        .order_by(order_status_history.c.id)
    ).mappings()
    return [dict(r) for r in rows]
