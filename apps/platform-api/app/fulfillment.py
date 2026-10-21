"""결제·배송·취소·청약철회 공통 — 주문 상태 전이 (커머스 확장, 판단기록 F3·F4·F6)

상태 (db 0011·0013·0014):
    PENDING_PAYMENT ─(PG 승인)─▶ PAID ─(관리자 상품 준비)─▶ PREPARING ─(택배 집하 통지)─▶ SHIPPING
      └─(30분 미결제)─▶ PAYMENT_EXPIRED     │                    │
    AWAITING_DEPOSIT ─(관리자 입금 확인)─▶ ┘                    │
      ├─(3일 미입금)─▶ DEPOSIT_EXPIRED     └──(고객 취소)────────┴──▶ CANCELLED
      └─(고객 취소)─────────────────────────────────────────────────▶ CANCELLED
    SHIPPING ─(택배 배송완료 통지)─▶ DELIVERED ─(7일 이내 청약철회)─▶ RETURN_REQUESTED
      ─(택배 회수 완료 후 관리자 확인)─▶ RETURNED

- 결제: 주문을 결제대기로 만든 뒤 브라우저가 PG 결제창(작은 창)을 연다. 돌아온 paymentKey로
  서버가 PG 승인 API를 불러 성공하면 PAID (F6). 30분 안에 승인되지 않으면 만료
- 배송: PAID → PREPARING은 관리자. 그다음 관리자가 "택배 접수"(택배 접수 API)를 하면 택배사가
  집하·배송완료를 통지하고, 그 통지로 SHIPPING·DELIVERED가 된다(쇼핑몰은 상태를 받기만 한다)
- 고객 취소는 상품준비중까지(F3). 택배 접수 뒤라면 택배 접수 취소가 먼저(집하 전만 가능)
- 청약철회 기간: 배송완료(재화 공급)일부터 7일 (전자상거래법 제17조제1항).
  신청하면 택배 회수를 접수하고, 택배사가 판매자 도착을 통지한 뒤 관리자가 회수 확인 → 반품완료
- 무통장입금(F7): 은행 연동 없이 관리자가 입금자명·금액을 보고 입금 확인 → PAID. 3일 안에 입금
  확인이 안 되면 입금 기한 경과
- 환불: 카드는 PG 승인 취소(바로 완료). 무통장입금은 고객이 마이페이지 환불계좌 목록에서 고른
  계좌(사본)로 관리자가 이체한 뒤 "환불 완료" 처리 — 그때까지 환불 대기(F7·F7-1).
  입금 전에 취소한 무통장입금 주문은 환불할 돈이 없다
- 상태가 바뀔 때마다 order_status_history 1행 — 계약·청약철회 기록 (전자상거래법 제6조)
- 여러 요청이 동시에 같은 주문을 바꾸지 않게 주문 행을 잠그고(FOR UPDATE) 현재 상태를 확인한다
- 연동 이전 데이터(시드·0013 이전 주문): PG·택배 서비스에 기록이 없다. 결제 취소는 쇼핑몰 안에서만
  기록하고, 택배 접수 없이 배송중인 주문은 관리자가 배송완료로 넘길 수 있다
"""

import secrets
from datetime import datetime, timedelta

from sqlalchemy import Connection, insert, select, update

from app.commerce import SHOP_DEPOSIT_ACCOUNT
from app.crypto import FieldCipher, claim_refund_context, refund_account_context
from app.errors import ApiError
from app.models import (
    bank_deposit,
    member,
    order_claim,
    order_status_history,
    orders,
    payment,
    product,
    refund_account,
)
from app.partners import SHOP_SENDER, Partners, pg_order_id
from app.partners.courier import CourierError
from app.partners.http import PartnerUnavailable
from app.partners.pg import PgError

STATUSES = (
    "PENDING_PAYMENT",
    "PAYMENT_EXPIRED",
    "AWAITING_DEPOSIT",
    "DEPOSIT_EXPIRED",
    "PAID",
    "PREPARING",
    "SHIPPING",
    "DELIVERED",
    "CANCELLED",
    "RETURN_REQUESTED",
    "RETURNED",
)
# 고객 주문 내역에 보이는 상태 — 결제하지 않은 주문은 내역에 없다
CUSTOMER_VISIBLE = tuple(s for s in STATUSES if s not in ("PENDING_PAYMENT", "PAYMENT_EXPIRED"))
# 배송 관리 화면에 나오는 상태 — 결제가 끝난 주문만
FULFILLMENT_STATUSES = tuple(
    s for s in CUSTOMER_VISIBLE if s not in ("AWAITING_DEPOSIT", "DEPOSIT_EXPIRED")
)
CANCELLABLE = frozenset({"AWAITING_DEPOSIT", "PAID", "PREPARING"})
WITHDRAWAL_PERIOD = timedelta(days=7)  # 전자상거래법 제17조제1항
PAYMENT_TIMEOUT = timedelta(minutes=30)
DEPOSIT_PERIOD = timedelta(days=3)  # 무통장입금 기한
# 탈퇴를 막는 진행 중 상태 — 배송·반품이 끝나지 않은 주문의 배송 정보를 지우면 업무가 끊긴다
# 입금대기도 포함 — 고객이 이미 입금했을 수 있다(취소한 뒤 탈퇴)
IN_PROGRESS = frozenset({"AWAITING_DEPOSIT", "PAID", "PREPARING", "SHIPPING", "RETURN_REQUESTED"})

MOCK_COURIER = "데모택배(가상)"
CANCEL_REASONS = ("CHANGE_OF_MIND", "WRONG_ORDER", "DELAY", "OTHER")
RETURN_REASONS = ("CHANGE_OF_MIND", "DEFECT", "WRONG_ITEM", "OTHER")

# 택배사 상태의 진행 순서 — 늦게 도착한 옛 통지로 상태가 되돌아가지 않게
DELIVERY_STEPS = {
    "RECEIVED": 0,
    "PICKED_UP": 1,
    "IN_TRANSIT": 2,
    "OUT_FOR_DELIVERY": 3,
    "DELIVERED": 4,
}
RETURN_STEPS = {
    "RETURN_RECEIVED": 0,
    "RETURN_PICKED_UP": 1,
    "RETURN_IN_TRANSIT": 2,
    "RETURN_DELIVERED": 3,
}


def mock_pg_cancel_tid() -> str:
    """연동 이전 결제의 취소 거래번호 — PG에 기록이 없어 쇼핑몰 안에서만 남긴다"""
    return "MOCKPG-CANCEL-" + secrets.token_hex(8).upper()


def is_linked_payment(pg_tid: str | None) -> bool:
    """가상 PG 서비스를 거친 결제인가 (paymentKey는 pay_로 시작) — 아니면 연동 이전 데이터"""
    return bool(pg_tid) and pg_tid.startswith("pay_")


def expected_delivery(ordered_at: datetime, ship_days: int) -> datetime:
    return ordered_at + timedelta(days=ship_days)


def withdrawal_deadline(delivered_at: datetime | None) -> datetime | None:
    return delivered_at + WITHDRAWAL_PERIOD if delivered_at else None


def _parse_time(value: str | None, default: datetime) -> datetime:
    try:
        return datetime.fromisoformat(value) if value else default
    except ValueError:
        return default


def _unavailable(partner: str) -> ApiError:
    return ApiError(502, f"{partner}_UNAVAILABLE", f"{partner.lower()} service unavailable")


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
) -> dict:
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
    return {**order, **values, "status": to_status}


# ── 결제 ─────────────────────────────────────────────────


def expire_stale_payments(
    conn: Connection, now: datetime, member_id: int | None = None, *, all_pending: bool = False
) -> None:
    """30분 안에 결제하지 않은 결제대기 주문을 만료로, 기한(3일)이 지난 입금대기 주문을 입금 기한
    경과로 (처리자 SYSTEM) — 조회·주문할 때마다 정리.
    all_pending이면 결제대기는 시간과 관계없이 모두(탈퇴할 때)"""
    _expire_deposits(conn, now, member_id)
    query = (
        update(orders)
        .where(orders.c.status == "PENDING_PAYMENT")
        .values(status="PAYMENT_EXPIRED")
        .returning(orders.c.id)
    )
    if not all_pending:
        query = query.where(orders.c.ordered_at < now - PAYMENT_TIMEOUT)
    if member_id is not None:
        query = query.where(orders.c.member_id == member_id)
    expired = conn.execute(query).scalars().all()
    if expired:
        conn.execute(
            insert(order_status_history),
            [
                {
                    "order_id": order_id,
                    "from_status": "PENDING_PAYMENT",
                    "to_status": "PAYMENT_EXPIRED",
                    "actor_type": "SYSTEM",
                    "changed_at": now,
                }
                for order_id in expired
            ],
        )


def _expire_deposits(conn: Connection, now: datetime, member_id: int | None) -> None:
    overdue = select(bank_deposit.c.order_id).where(
        bank_deposit.c.due_at < now, bank_deposit.c.confirmed_at.is_(None)
    )
    query = (
        update(orders)
        .where(orders.c.status == "AWAITING_DEPOSIT", orders.c.id.in_(overdue))
        .values(status="DEPOSIT_EXPIRED")
        .returning(orders.c.id)
    )
    if member_id is not None:
        query = query.where(orders.c.member_id == member_id)
    expired = conn.execute(query).scalars().all()
    if expired:
        conn.execute(
            insert(order_status_history),
            [
                {
                    "order_id": order_id,
                    "from_status": "AWAITING_DEPOSIT",
                    "to_status": "DEPOSIT_EXPIRED",
                    "actor_type": "SYSTEM",
                    "changed_at": now,
                }
                for order_id in expired
            ],
        )


def open_deposit(conn: Connection, order, now: datetime, depositor_name: str) -> dict:
    """무통장입금 주문: 쇼핑몰 입금 계좌(주문 시점 사본)·입금자명·기한 기록 → 고객에게 안내할 값"""
    values = {
        **SHOP_DEPOSIT_ACCOUNT,
        "depositor_name": depositor_name,
        "due_at": now + DEPOSIT_PERIOD,
    }
    conn.execute(insert(bank_deposit).values(order_id=order["id"], **values))
    return values


def confirm_deposit(conn: Connection, order, now: datetime, operator_id: int) -> str:
    """관리자: 입금 확인 → 결제(BANK_TRANSFER) 기록 → PAID. 기한이 지났으면 입금 기한 경과로

    결과: PAID / EXPIRED (만료는 상태를 바꾼 뒤 커밋해야 하므로 예외 대신 결과로 돌려준다)
    """
    if order["status"] != "AWAITING_DEPOSIT":
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot confirm deposit in {order['status']}")
    deposit = (
        conn.execute(select(bank_deposit).where(bank_deposit.c.order_id == order["id"]))
        .mappings()
        .one()
    )
    if deposit["due_at"] < now:
        change_status(conn, order, "DEPOSIT_EXPIRED", now, actor_type="SYSTEM")
        return "EXPIRED"
    conn.execute(
        update(bank_deposit)
        .where(bank_deposit.c.order_id == order["id"])
        .values(confirmed_at=now, confirmed_by=operator_id)
    )
    conn.execute(
        insert(payment).values(
            order_id=order["id"], method="BANK_TRANSFER", amount=order["amount"], approved_at=now
        )
    )
    change_status(conn, order, "PAID", now, actor_type="OPERATOR", operator_id=operator_id)
    return "PAID"


def payment_method(conn: Connection, order_id: int) -> str | None:
    """결제 수단 — 결제 전(입금대기 등)이면 None"""
    return conn.execute(select(payment.c.method).where(payment.c.order_id == order_id)).scalar()


def refund_account_snapshot(
    conn: Connection, cipher: FieldCipher, member_id: int, account_id: int, order_id: int
) -> dict:
    """고객이 고른 환불계좌를 취소·반품 기록에 복사할 값으로 — 계좌번호는 주문에 묶어 다시 암호화"""
    account = (
        conn.execute(
            select(refund_account).where(
                refund_account.c.id == account_id, refund_account.c.member_id == member_id
            )
        )
        .mappings()
        .first()
    )
    if account is None:
        raise ApiError(400, "REFUND_ACCOUNT_REQUIRED", "choose one of your refund accounts")
    number = cipher.decrypt(account["account_number_enc"], refund_account_context(member_id))
    return {
        "refund_method": "BANK_TRANSFER",
        "refund_bank_name": account["bank_name"],
        "refund_account_holder": account["account_holder"],
        "refund_account_enc": cipher.encrypt(number, claim_refund_context(order_id)),
        "refund_account_last4": account["account_last4"],
    }


def payment_request(conn: Connection, order, partners: Partners) -> dict:
    """브라우저가 PG 결제창(작은 창)에 POST할 값 — 구매자 이름·이메일·휴대폰 포함 (F6 Q1)

    돌아갈 주소(successUrl·failUrl)는 화면이 자기 출처로 붙인다. PG는 등록된 출처만 허용한다.
    """
    buyer = conn.execute(
        select(member.c.name, member.c.email, member.c.phone).where(
            member.c.id == order["member_id"]
        )
    ).first()
    name = conn.execute(select(product.c.name).where(product.c.id == order["product_id"]))
    return {
        "checkout_url": partners.pg.checkout_url,
        "fields": {
            "clientKey": partners.pg.client_key,
            "orderId": pg_order_id(order["id"]),
            "orderName": name.scalar_one(),
            "amount": str(order["amount"]),
            "customerName": buyer.name,
            "customerEmail": buyer.email,
            "customerMobilePhone": (buyer.phone or "").replace("-", ""),
        },
    }


def confirm_payment(
    conn: Connection, order, now: datetime, partners: Partners, payment_key: str, amount: int
) -> str:
    """결제창에서 돌아온 paymentKey로 PG 승인 → 결제 결과 저장 → PAID

    결과: PAID / ALREADY_PAID / EXPIRED. 만료는 상태를 바꾼 뒤 커밋해야 하므로 예외 대신 결과로
    돌려준다(호출한 쪽이 커밋 후 오류 응답).
    """
    if order["status"] == "PAID":
        paid = conn.execute(
            select(payment.c.pg_tid).where(payment.c.order_id == order["id"])
        ).scalar()
        if paid == payment_key:
            return "ALREADY_PAID"  # 같은 승인을 다시 요청(새로고침 등) — 이미 처리됨
    if order["status"] != "PENDING_PAYMENT":
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot pay in {order['status']}")
    if order["ordered_at"] < now - PAYMENT_TIMEOUT:
        change_status(conn, order, "PAYMENT_EXPIRED", now, actor_type="SYSTEM")
        return "EXPIRED"
    if amount != order["amount"]:
        # 결제창에서 돌아온 금액이 주문 금액과 다르면 승인을 요청하지 않는다
        raise ApiError(400, "AMOUNT_MISMATCH", "amount does not match the order")
    try:
        result = partners.pg.confirm(payment_key, pg_order_id(order["id"]), order["amount"])
    except PartnerUnavailable as error:
        raise _unavailable("PG") from error
    except PgError as error:
        raise ApiError(400, "PAYMENT_FAILED", error.code) from error
    card = result.get("card") or {}
    if result.get("status") != "DONE" or result.get("totalAmount") != order["amount"]:
        raise ApiError(400, "PAYMENT_FAILED", "unexpected approval result")
    conn.execute(
        insert(payment).values(
            order_id=order["id"],
            method="CARD",
            card_company=card.get("company") or "-",
            pg_tid=result["paymentKey"],
            amount=result["totalAmount"],
            approved_at=_parse_time(result.get("approvedAt"), now),
            card_number_masked=card.get("number"),
            approve_no=card.get("approveNo"),
            installment_months=card.get("installmentPlanMonths"),
            receipt_url=(result.get("receipt") or {}).get("url"),
        )
    )
    change_status(conn, order, "PAID", now, actor_type="CUSTOMER")
    return "PAID"


def _refund(
    conn: Connection, order_id: int, partners: Partners, reason: str, now: datetime
) -> dict:
    """환불 → 취소·반품 기록에 남길 값

    - 카드: PG 승인 취소(연동 이전 결제는 쇼핑몰 안에서만 기록) → 바로 환불 완료
    - 무통장입금: 관리자가 이체한 뒤 완료 처리 → 환불 대기 (계좌 사본은 호출한 쪽이 넣는다)
    - 결제 전(입금대기 취소): 환불할 돈이 없다 → 빈 값
    """
    paid = conn.execute(
        select(payment.c.amount, payment.c.pg_tid, payment.c.method).where(
            payment.c.order_id == order_id
        )
    ).first()
    if paid is None:
        return {}
    if paid.method == "BANK_TRANSFER":
        return {"refund_amount": paid.amount, "refund_status": "PENDING"}
    done = {"refund_method": "PG_CANCEL", "refund_status": "DONE", "refunded_at": now}
    if not is_linked_payment(paid.pg_tid):
        return {**done, "refund_amount": paid.amount, "refund_pg_tid": mock_pg_cancel_tid()}
    try:
        result = partners.pg.cancel(paid.pg_tid, reason)
    except PartnerUnavailable as error:
        raise _unavailable("PG") from error
    except PgError as error:
        raise ApiError(502, "PG_CANCEL_FAILED", error.code) from error
    cancels = result.get("cancels") or []
    if result.get("status") != "CANCELED" or not cancels:
        raise ApiError(502, "PG_CANCEL_FAILED", "unexpected cancel result")
    return {
        **done,
        "refund_amount": cancels[-1]["cancelAmount"],
        "refund_pg_tid": cancels[-1]["transactionKey"],
    }


# ── 배송 ─────────────────────────────────────────────────


def prepare_order(conn: Connection, order, now: datetime, operator_id: int) -> str:
    """관리자: 결제완료 → 상품준비중"""
    if order["status"] != "PAID":
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot prepare in {order['status']}")
    change_status(conn, order, "PREPARING", now, actor_type="OPERATOR", operator_id=operator_id)
    return "PREPARING"


def complete_legacy_delivery(conn: Connection, order, now: datetime, operator_id: int) -> str:
    """관리자: 연동 이전 주문(택배 접수 없이 배송중)을 배송완료로"""
    if order["status"] != "SHIPPING" or order["courier_shipment_id"] is not None:
        raise ApiError(409, "INVALID_ORDER_STATUS", "delivery is tracked by the courier")
    change_status(
        conn,
        order,
        "DELIVERED",
        now,
        actor_type="OPERATOR",
        operator_id=operator_id,
        delivered_at=now,
    )
    return "DELIVERED"


def _items(conn: Connection, order) -> list[dict]:
    name = conn.execute(select(product.c.name).where(product.c.id == order["product_id"]))
    return [{"name": name.scalar_one(), "quantity": 1}]


def _customer_party(order) -> dict:
    return {
        "name": order["ship_recipient"],
        "phone": order["ship_phone"],
        "zipCode": order["ship_zip_code"],
        "address": order["ship_address"],
        "addressDetail": order["ship_address_detail"],
    }


def dispatch_order(conn: Connection, order, now: datetime, partners: Partners) -> dict:
    """관리자: 택배 접수 — 받는 사람·배송 메모·상품·주문번호·보내는 곳만 보낸다 (F6 Q2)

    주문 상태는 상품준비중 그대로. 택배사가 집하를 통지하면 배송중이 된다.
    같은 주문을 다시 접수하면 택배 서비스가 기존 접수를 돌려준다(재시도 안전).
    """
    if order["status"] != "PREPARING":
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot dispatch in {order['status']}")
    if order["courier_shipment_id"] is not None:
        raise ApiError(409, "ALREADY_DISPATCHED", "order already dispatched")
    if not order["ship_recipient"]:
        raise ApiError(409, "SHIPPING_INFO_MISSING", "order has no shipping snapshot")
    try:
        shipment = partners.courier.create_shipment(
            {
                "orderNo": pg_order_id(order["id"]),
                "sender": SHOP_SENDER,
                "receiver": _customer_party(order),
                "memo": order["delivery_memo"],
                "items": _items(conn, order),
            }
        )
    except PartnerUnavailable as error:
        raise _unavailable("COURIER") from error
    except CourierError as error:
        raise ApiError(502, "COURIER_REJECTED", error.code) from error
    conn.execute(
        update(orders)
        .where(orders.c.id == order["id"])
        .values(
            courier=shipment.get("courier") or MOCK_COURIER,
            courier_shipment_id=shipment["shipmentId"],
            courier_status=shipment["status"],
            courier_synced_at=now,
        )
    )
    return shipment


def apply_shipment(conn: Connection, shipment: dict, now: datetime) -> str:
    """택배사가 알려 준 상태를 반영 (통지·조회 공통). 결과: applied / stale / unknown / ignored

    - 배송: 집하 이후면 배송중(송장번호·발송 시각), 배송완료면 배송완료(도착 시각). 처리자 SYSTEM
    - 반품 회수: 회수 상태·회수 송장번호만 기록(반품완료는 관리자가 회수 확인 후)
    - 순서가 뒤로 가는 상태(늦게 도착한 옛 통지, 택배사 DB에서 되돌린 값)는 반영하지 않는다
    """
    status = shipment.get("status")
    if shipment.get("kind") == "RETURN":
        return _apply_return(conn, shipment, status, now)
    order = (
        conn.execute(
            select(orders)
            .where(orders.c.courier_shipment_id == shipment.get("shipmentId"))
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if order is None:
        return "unknown"
    if status == "CANCELED":
        conn.execute(
            update(orders)
            .where(orders.c.id == order["id"])
            .values(courier_status="CANCELED", courier_synced_at=now)
        )
        return "applied"
    step = DELIVERY_STEPS.get(status)
    if step is None:
        return "ignored"
    if step < DELIVERY_STEPS.get(order["courier_status"], -1):
        return "stale"
    tracking_no = shipment.get("trackingNo") or order["tracking_no"]
    conn.execute(
        update(orders)
        .where(orders.c.id == order["id"])
        .values(courier_status=status, courier_synced_at=now, tracking_no=tracking_no)
    )
    if order["status"] not in ("PREPARING", "SHIPPING"):
        return "applied"  # 이미 취소 등 — 주문 상태는 그대로
    if step >= DELIVERY_STEPS["PICKED_UP"] and order["status"] == "PREPARING":
        order = change_status(
            conn,
            order,
            "SHIPPING",
            now,
            actor_type="SYSTEM",
            courier=shipment.get("courier") or MOCK_COURIER,
            tracking_no=tracking_no,
            shipped_at=_parse_time(shipment.get("pickedUpAt"), now),
        )
    if step == DELIVERY_STEPS["DELIVERED"] and order["status"] == "SHIPPING":
        change_status(
            conn,
            order,
            "DELIVERED",
            now,
            actor_type="SYSTEM",
            delivered_at=_parse_time(shipment.get("deliveredAt"), now),
        )
    return "applied"


def _apply_return(conn: Connection, shipment: dict, status, now: datetime) -> str:
    claim = (
        conn.execute(
            select(order_claim)
            .where(order_claim.c.pickup_shipment_id == shipment.get("shipmentId"))
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if claim is None:
        return "unknown"
    if status != "CANCELED":
        step = RETURN_STEPS.get(status)
        if step is None:
            return "ignored"
        if step < RETURN_STEPS.get(claim["pickup_status"], -1):
            return "stale"
    conn.execute(
        update(order_claim)
        .where(order_claim.c.id == claim["id"])
        .values(
            pickup_status=status,
            pickup_tracking_no=shipment.get("trackingNo") or claim["pickup_tracking_no"],
        )
    )
    return "applied"


def sync_order(conn: Connection, order, now: datetime, partners: Partners) -> list[str]:
    """관리자 "배송 조회": 택배 서비스에서 배송·회수 상태를 다시 가져와 반영 (통지를 놓쳤을 때)"""
    ids = [order["courier_shipment_id"]] if order["courier_shipment_id"] else []
    ids += [
        c["pickup_shipment_id"]
        for c in claims_of(conn, [order["id"]]).get(order["id"], [])
        if c["pickup_shipment_id"]
    ]
    if not ids:
        raise ApiError(409, "NOT_DISPATCHED", "order has no courier shipment")
    results = []
    for shipment_id in ids:
        try:
            shipment = partners.courier.get(shipment_id)
        except PartnerUnavailable as error:
            raise _unavailable("COURIER") from error
        except CourierError as error:
            raise ApiError(502, "COURIER_REJECTED", error.code) from error
        results.append(apply_shipment(conn, shipment, now))
    return results


# ── 취소·청약철회 ─────────────────────────────────────────


def cancel_order(
    conn: Connection,
    order,
    now: datetime,
    reason_code: str,
    detail: str | None,
    partners: Partners,
    refund_to: dict | None = None,
) -> None:
    """고객: 상품준비중까지 취소 → (택배 접수 취소) → 환불(카드 PG 취소 / 무통장입금 이체 대기)

    refund_to: 무통장입금 결제의 환불계좌 사본(refund_account_snapshot) — 입금 확인된 주문만 필요
    """
    if order["status"] not in CANCELLABLE:
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot cancel in {order['status']}")
    if order["courier_shipment_id"] and order["courier_status"] != "CANCELED":
        try:
            partners.courier.cancel(order["courier_shipment_id"])
        except PartnerUnavailable as error:
            raise _unavailable("COURIER") from error
        except CourierError as error:
            if error.code == "ALREADY_PICKED_UP":
                # 통지가 오기 전에 이미 집하됨 — 배송완료 후 청약철회로
                raise ApiError(409, "ALREADY_SHIPPED", "already picked up by courier") from error
            raise ApiError(502, "COURIER_REJECTED", error.code) from error
        conn.execute(
            update(orders).where(orders.c.id == order["id"]).values(courier_status="CANCELED")
        )
    refund = _refund(conn, order["id"], partners, f"고객 주문 취소 ({reason_code})", now)
    if refund.get("refund_status") == "PENDING":
        if refund_to is None:
            raise ApiError(400, "REFUND_ACCOUNT_REQUIRED", "choose one of your refund accounts")
        refund |= refund_to
    conn.execute(
        insert(order_claim).values(
            order_id=order["id"],
            claim_type="CANCEL",
            reason_code=reason_code,
            reason_detail=detail,
            status="COMPLETED",
            requested_at=now,
            completed_at=now,
            **refund,
        )
    )
    change_status(conn, order, "CANCELLED", now, actor_type="CUSTOMER")


def request_return(
    conn: Connection,
    order,
    now: datetime,
    reason_code: str,
    detail: str | None,
    partners: Partners,
    refund_to: dict | None = None,
) -> None:
    """고객: 배송완료 후 7일 이내 청약철회 신청 → 반품접수 + 택배 회수 접수

    무통장입금 결제면 환불받을 계좌(refund_to)를 신청할 때 고른다 — 반품 완료 후 그 계좌로 이체
    """
    if order["status"] != "DELIVERED":
        raise ApiError(409, "INVALID_ORDER_STATUS", f"cannot return in {order['status']}")
    deadline = withdrawal_deadline(order["delivered_at"])
    if deadline is None or now > deadline:
        raise ApiError(409, "WITHDRAWAL_PERIOD_EXPIRED", "return period (7 days) has passed")
    if not order["ship_recipient"]:
        raise ApiError(409, "SHIPPING_INFO_MISSING", "order has no shipping snapshot")
    if payment_method(conn, order["id"]) == "BANK_TRANSFER" and refund_to is None:
        raise ApiError(400, "REFUND_ACCOUNT_REQUIRED", "choose one of your refund accounts")
    try:
        pickup = partners.courier.create_return(
            {
                "orderNo": pg_order_id(order["id"]),
                "pickup": _customer_party(order),
                "returnTo": SHOP_SENDER,
                "originalTrackingNo": order["tracking_no"],
                "items": _items(conn, order),
            }
        )
    except PartnerUnavailable as error:
        raise _unavailable("COURIER") from error
    except CourierError as error:
        raise ApiError(502, "COURIER_REJECTED", error.code) from error
    conn.execute(
        insert(order_claim).values(
            order_id=order["id"],
            claim_type="RETURN",
            reason_code=reason_code,
            reason_detail=detail,
            status="REQUESTED",
            requested_at=now,
            pickup_shipment_id=pickup["shipmentId"],
            pickup_status=pickup["status"],
            **(refund_to or {}),
        )
    )
    change_status(conn, order, "RETURN_REQUESTED", now, actor_type="CUSTOMER")


def complete_return(
    conn: Connection, order, now: datetime, operator_id: int, partners: Partners
) -> None:
    """관리자: 반품 상품 회수 확인 → 반품완료 + 환불(카드 PG 취소 / 무통장입금 이체 대기)

    택배 회수를 접수한 반품은 택배사가 판매자 도착(RETURN_DELIVERED)을 알려 온 뒤에만.
    """
    if order["status"] != "RETURN_REQUESTED":
        raise ApiError(409, "INVALID_ORDER_STATUS", f"no return requested ({order['status']})")
    claim = (
        conn.execute(
            select(order_claim).where(
                order_claim.c.order_id == order["id"],
                order_claim.c.claim_type == "RETURN",
                order_claim.c.status == "REQUESTED",
            )
        )
        .mappings()
        .first()
    )
    if claim is None:
        raise ApiError(409, "INVALID_ORDER_STATUS", "no return requested")
    if claim["pickup_shipment_id"] and claim["pickup_status"] != "RETURN_DELIVERED":
        raise ApiError(409, "RETURN_NOT_ARRIVED", "returned item has not arrived yet")
    refund = _refund(conn, order["id"], partners, "청약철회 반품 완료", now)
    conn.execute(
        update(order_claim)
        .where(order_claim.c.id == claim["id"])
        .values(status="COMPLETED", completed_at=now, completed_by=operator_id, **refund)
    )
    change_status(conn, order, "RETURNED", now, actor_type="OPERATOR", operator_id=operator_id)


def pending_refund_claim(conn: Connection, order_id: int):
    """이체 환불을 기다리는 취소·반품 기록 (무통장입금) — 없으면 None"""
    return (
        conn.execute(
            select(order_claim).where(
                order_claim.c.order_id == order_id, order_claim.c.refund_status == "PENDING"
            )
        )
        .mappings()
        .first()
    )


def complete_refund(conn: Connection, order, now: datetime, operator_id: int) -> None:
    """관리자: 고객 환불계좌로 이체한 뒤 환불 완료 처리 (무통장입금, 은행 연동 없음 — F7)"""
    claim = pending_refund_claim(conn, order["id"])
    if claim is None:
        raise ApiError(409, "NO_PENDING_REFUND", "no refund is waiting for a transfer")
    conn.execute(
        update(order_claim)
        .where(order_claim.c.id == claim["id"])
        .values(refund_status="DONE", refunded_at=now, refunded_by=operator_id)
    )


def claims_of(conn: Connection, order_ids: list[int]) -> dict[int, list[dict]]:
    if not order_ids:
        return {}
    rows = conn.execute(
        select(order_claim).where(order_claim.c.order_id.in_(order_ids)).order_by(order_claim.c.id)
    ).mappings()
    result: dict[int, list[dict]] = {}
    for r in rows:
        # 환불계좌 암호문은 응답에 싣지 않는다 — 전체 번호는 "계좌 전체 보기"로만(결제수단 기록)
        claim = {k: v for k, v in r.items() if k != "refund_account_enc"}
        result.setdefault(r["order_id"], []).append(claim)
    return result


def history_of(conn: Connection, order_id: int) -> list[dict]:
    rows = conn.execute(
        select(order_status_history)
        .where(order_status_history.c.order_id == order_id)
        .order_by(order_status_history.c.id)
    ).mappings()
    return [dict(r) for r in rows]
