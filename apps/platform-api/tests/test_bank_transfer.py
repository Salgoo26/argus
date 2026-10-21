"""무통장입금·환불계좌 선택 (커머스 확장, 판단기록 F7·F7-1)

입금대기 → 관리자 입금 확인 → 결제완료. 입금 확인된 주문을 취소·반품하면 고객이 마이페이지
환불계좌 목록에서 고른 계좌(사본)로 관리자가 이체한 뒤 환불 완료 처리.
"""

from datetime import timedelta

from sqlalchemy import select, update

from app.crypto import FieldCipher, claim_refund_context
from app.models import bank_deposit, order_claim, orders, payment, retained_member_record

from conftest import (
    CUSTOMER_PASSWORD,
    TEST_PAYMENT_KEY,
    add_operator,
    courier_notify,
    login,
    outbox_payloads,
    place_order,
    signup,
)

HOME = {
    "label": "집",
    "recipient": "홍길동",  # 가상
    "phone": "010-0000-1234",
    "zip_code": "90001",
    "address": "서울특별시 가상구 가상로 1",
}
ACCOUNT = {"bank_name": "바다은행", "account_holder": "홍길동", "account_number": "3330001234567"}


def _customer(client, email: str = "buyer@example.com") -> int:
    signup(client, email=email)
    res = client.post("/shop/me/addresses", json=HOME)
    return max(i["id"] for i in res.json()["items"])


def _bank_order(client, address_id: int, depositor: str | None = "홍길동") -> dict:
    res = client.post(
        "/shop/orders",
        json={
            "product_id": 1,
            "shipping_address_id": address_id,
            "payment_method": "BANK_TRANSFER",
            "depositor_name": depositor,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _admin(client, engine, password_hash, role: str = "OPS", login_id: str = "ops_park"):
    add_operator(engine, password_hash, login_id=login_id, role=role)
    assert login(client, login_id).status_code == 200


def _post(client, path: str, status: int = 200, **kw):
    res = client.post(path, **kw)
    assert res.status_code == status, res.text
    return res.json()


def _my_order(client, order_id: int) -> dict:
    return next(o for o in client.get("/shop/orders").json()["items"] if o["id"] == order_id)


def _paid_bank_order(client, engine, password_hash) -> dict:
    """무통장입금 주문 → 관리자 입금 확인까지"""
    created = _bank_order(client, _customer(client))
    _admin(client, engine, password_hash)
    _post(client, f"/admin/orders/{created['order']['id']}/deposit-confirm")
    return created["order"]


def _account_id(client, account: dict = ACCOUNT) -> int:
    res = client.post("/shop/me/refund-accounts", json=account)
    assert res.status_code == 201, res.text
    return res.json()["items"][-1]["id"]


# ── 주문: 입금대기 ───────────────────────────────────────────


def test_bank_order_awaits_deposit_with_shop_account_and_due_date(client, engine, partners):
    created = _bank_order(client, _customer(client))

    assert created["payment_request"] is None  # PG 결제창을 열지 않는다
    order = created["order"]
    assert order["status"] == "AWAITING_DEPOSIT" and order["payment_method"] is None
    assert order["deposit_bank_name"] == "하늘은행" and order["deposit_account_holder"]
    assert order["depositor_name"] == "홍길동"
    # 입금대기는 고객 주문 내역에 보인다(입금할 계좌를 다시 봐야 하므로)
    assert _my_order(client, order["id"])["status"] == "AWAITING_DEPOSIT"
    with engine.connect() as conn:
        deposit = conn.execute(select(bank_deposit)).mappings().one()
        ordered_at = conn.execute(select(orders.c.ordered_at)).scalar_one()
    assert deposit["due_at"] - ordered_at == timedelta(days=3)
    assert partners.pg.calls == []


def test_bank_order_needs_a_depositor_name(client, partners):
    address_id = _customer(client)
    res = client.post(
        "/shop/orders",
        json={
            "product_id": 1,
            "shipping_address_id": address_id,
            "payment_method": "BANK_TRANSFER",
        },
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "DEPOSITOR_NAME_REQUIRED"


def test_admin_confirms_deposit_and_it_is_an_order_update(client, engine, password_hash):
    order = _paid_bank_order(client, engine, password_hash)

    detail = client.get(f"/admin/orders/{order['id']}").json()
    assert detail["status"] == "PAID" and detail["method"] == "BANK_TRANSFER"
    assert detail["deposit_confirmed_at"] and detail["deposit_confirmed_by"]
    with engine.connect() as conn:
        paid = conn.execute(select(payment)).mappings().one()
    assert paid["card_company"] is None and paid["pg_tid"] is None and paid["amount"] > 0
    [event] = [
        p
        for p in outbox_payloads(engine)
        if p["request"]["path"] == "/admin/orders/{order_id}/deposit-confirm"
    ]
    assert event["action"] == "UPDATE" and event["data_category"] == "ORDER"
    assert event["subject"]["count"] == 1
    # 두 번 확인할 수 없다
    _post(client, f"/admin/orders/{order['id']}/deposit-confirm", 409)


def test_overdue_deposit_expires_and_cannot_be_confirmed(client, engine, password_hash):
    created = _bank_order(client, _customer(client))
    order_id = created["order"]["id"]
    with engine.begin() as conn:
        conn.execute(update(bank_deposit).values(due_at=bank_deposit.c.due_at - timedelta(days=4)))
    _admin(client, engine, password_hash)
    res = client.post(f"/admin/orders/{order_id}/deposit-confirm")
    assert res.status_code == 409 and res.json()["error"]["code"] == "DEPOSIT_EXPIRED"
    assert _my_order(client, order_id)["status"] == "DEPOSIT_EXPIRED"


def test_customer_listing_expires_overdue_deposits(client, engine):
    created = _bank_order(client, _customer(client))
    with engine.begin() as conn:
        conn.execute(update(bank_deposit).values(due_at=bank_deposit.c.due_at - timedelta(days=4)))
    assert _my_order(client, created["order"]["id"])["status"] == "DEPOSIT_EXPIRED"


def test_cancel_before_deposit_needs_no_refund(client, engine, partners):
    created = _bank_order(client, _customer(client))
    order_id = created["order"]["id"]

    body = _post(client, f"/shop/orders/{order_id}/cancel", json={"reason_code": "WRONG_ORDER"})
    assert body["status"] == "CANCELLED"
    [claim] = body["claims"]
    assert claim["refund_amount"] is None and claim["refund_status"] is None
    assert partners.pg.calls == []


def test_unconfirmed_bank_orders_stay_out_of_the_delivery_list(client, engine, password_hash):
    _bank_order(client, _customer(client))
    _admin(client, engine, password_hash)
    assert client.get("/admin/deliveries").json()["total"] == 0
    assert client.get("/admin/orders").json()["items"][0]["status"] == "AWAITING_DEPOSIT"


# ── 환불: 고객이 고른 계좌로 관리자가 이체 ───────────────────


def test_cancel_paid_bank_order_requires_one_of_my_refund_accounts(client, engine, password_hash):
    order = _paid_bank_order(client, engine, password_hash)

    res = client.post(f"/shop/orders/{order['id']}/cancel", json={"reason_code": "WRONG_ORDER"})
    assert res.status_code == 400 and res.json()["error"]["code"] == "REFUND_ACCOUNT_REQUIRED"
    res = client.post(
        f"/shop/orders/{order['id']}/cancel",
        json={"reason_code": "WRONG_ORDER", "refund_account_id": 999},
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "REFUND_ACCOUNT_REQUIRED"
    assert _my_order(client, order["id"])["status"] == "PAID"


def test_bank_refund_waits_for_transfer_then_admin_completes(client, engine, password_hash):
    order = _paid_bank_order(client, engine, password_hash)
    account_id = _account_id(client)

    body = _post(
        client,
        f"/shop/orders/{order['id']}/cancel",
        json={"reason_code": "CHANGE_OF_MIND", "refund_account_id": account_id},
    )
    [claim] = body["claims"]
    assert body["status"] == "CANCELLED"
    assert claim["refund_method"] == "BANK_TRANSFER" and claim["refund_status"] == "PENDING"
    assert claim["refund_bank_name"] == "바다은행" and claim["refund_account_last4"] == "4567"
    assert "3330001234567" not in str(body)

    # 고객이 마이페이지에서 계좌를 지워도 환불할 계좌는 사본으로 남아 있다
    client.delete(f"/shop/me/refund-accounts/{account_id}")
    with engine.connect() as conn:
        row = conn.execute(select(order_claim)).mappings().one()
    cipher = FieldCipher(bytes.fromhex(TEST_PAYMENT_KEY))
    assert cipher.decrypt(row["refund_account_enc"], claim_refund_context(order["id"])) == (
        "3330001234567"
    )

    # 관리자: 이체할 계좌 전체 번호(결제수단 조회) → 이체 → 환불 완료
    detail = client.get(f"/admin/orders/{order['id']}")
    assert "refund_account_enc" not in detail.text and "3330001234567" not in detail.text
    full = client.get(f"/admin/orders/{order['id']}/refund-account")
    assert full.status_code == 200 and full.json()["account_number"] == "3330001234567"
    [reveal] = [p for p in outbox_payloads(engine) if p["data_category"] == "PAYMENT"]
    assert reveal["action"] == "READ" and "3330001234567" not in str(reveal)

    done = _post(client, f"/admin/orders/{order['id']}/refund-complete")
    assert done["refund_status"] == "DONE"
    claim = _my_order(client, order["id"])["claims"][0]
    assert claim["refund_status"] == "DONE" and claim["refunded_at"]
    # 끝난 건의 계좌는 다시 열 수 없고, 두 번 완료할 수 없다
    assert client.get(f"/admin/orders/{order['id']}/refund-account").status_code == 404
    _post(client, f"/admin/orders/{order['id']}/refund-complete", 409)


def test_only_refund_viewers_can_complete_a_transfer(client, engine, password_hash):
    order = _paid_bank_order(client, engine, password_hash)
    _post(
        client,
        f"/shop/orders/{order['id']}/cancel",
        json={"reason_code": "CHANGE_OF_MIND", "refund_account_id": _account_id(client)},
    )
    _admin(client, engine, password_hash, role="MARKETING", login_id="mkt_lee")
    assert client.post(f"/admin/orders/{order['id']}/refund-complete").status_code == 403
    assert client.get(f"/admin/orders/{order['id']}/refund-account").status_code == 403


def test_bank_return_picks_account_at_request_and_refund_waits_after_arrival(
    client, engine, password_hash, partners
):
    order = _paid_bank_order(client, engine, password_hash)
    _post(client, f"/admin/deliveries/{order['id']}/advance")
    shipment_id = _post(client, f"/admin/deliveries/{order['id']}/dispatch")["courier_shipment_id"]
    courier_notify(client, partners.courier.set_status(shipment_id, "DELIVERED"))

    res = client.post(f"/shop/orders/{order['id']}/return", json={"reason_code": "DEFECT"})
    assert res.status_code == 400 and res.json()["error"]["code"] == "REFUND_ACCOUNT_REQUIRED"
    assert len(partners.courier.payloads) == 1  # 계좌를 고르기 전엔 회수 접수도 없다(배송 1건뿐)
    body = _post(
        client,
        f"/shop/orders/{order['id']}/return",
        json={"reason_code": "DEFECT", "refund_account_id": _account_id(client)},
    )
    [claim] = body["claims"]
    assert claim["refund_method"] == "BANK_TRANSFER" and claim["refund_status"] is None

    claims = client.get(f"/admin/orders/{order['id']}").json()["claims"]
    pickup_id = claims[0]["pickup_shipment_id"]
    courier_notify(client, partners.courier.set_status(pickup_id, "RETURN_DELIVERED"))
    _post(client, f"/admin/deliveries/{order['id']}/return-complete")
    claim = _my_order(client, order["id"])["claims"][0]
    assert claim["refund_status"] == "PENDING" and claim["refund_amount"] == order["amount"]
    assert partners.pg.calls == []


def test_card_refund_is_recorded_as_pg_cancel_done(client, engine, partners):
    order = place_order(client, partners, _customer(client))
    body = _post(client, f"/shop/orders/{order['id']}/cancel", json={"reason_code": "DELAY"})
    [claim] = body["claims"]
    assert claim["refund_method"] == "PG_CANCEL" and claim["refund_status"] == "DONE"


# ── 탈퇴 ─────────────────────────────────────────────────


def test_withdrawal_waits_for_awaiting_deposit_and_pending_refunds(client, engine, password_hash):
    created = _bank_order(client, _customer(client))
    withdraw = {"password": CUSTOMER_PASSWORD}
    res = client.post("/shop/me/withdraw", json=withdraw)
    assert res.status_code == 409 and res.json()["error"]["code"] == "ORDERS_IN_PROGRESS"

    _admin(client, engine, password_hash)
    _post(client, f"/admin/orders/{created['order']['id']}/deposit-confirm")
    _post(
        client,
        f"/shop/orders/{created['order']['id']}/cancel",
        json={"reason_code": "CHANGE_OF_MIND", "refund_account_id": _account_id(client)},
    )
    res = client.post("/shop/me/withdraw", json=withdraw)
    assert res.status_code == 409 and res.json()["error"]["code"] == "REFUND_IN_PROGRESS"

    _post(client, f"/admin/orders/{created['order']['id']}/refund-complete")
    assert client.post("/shop/me/withdraw", json=withdraw).status_code == 204
    with engine.connect() as conn:
        claim = conn.execute(select(order_claim)).mappings().one()
        deposit = conn.execute(select(bank_deposit)).mappings().one()
        retained = conn.execute(select(retained_member_record.c.data)).scalars().all()
    # 환불 계좌 사본·입금자명은 운영 테이블에서 지우고 분리보관으로
    assert claim["refund_account_enc"] is None and claim["refund_account_holder"] is None
    assert deposit["depositor_name"] == "(분리보관)"
    [record] = [r for r in retained if "orders" in r]
    [kept] = record["orders"]
    assert kept["payment_method"] == "BANK_TRANSFER" and kept["depositor_name"] == "홍길동"
    assert kept["claims"][0]["refund_account_last4"] == "4567"
