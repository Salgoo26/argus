"""결제(가상 PG)·배송(가상 택배)·취소·청약철회 (커머스 확장, 판단기록 F3·F4·F6)

가상 PG·택배 서비스는 conftest의 FakePg·FakeCourier로 대신한다(서비스 자체는 apps/mock-*의 테스트).
"""

from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

from app.main import create_app
from app.models import order_claim, order_status_history, orders, payment, retained_member_record

from conftest import (
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
    "address_detail": "101호",
}


def _address(client, email: str = "buyer@example.com") -> int:
    signup(client, email=email)
    res = client.post("/shop/me/addresses", json=HOME)
    return max(i["id"] for i in res.json()["items"])


def _place_order(client, partners, email: str = "buyer@example.com", **extra) -> dict:
    return place_order(client, partners, _address(client, email), **extra)


def _admin(client, engine, password_hash, **overrides):
    add_operator(engine, password_hash, **overrides)
    assert login(client, overrides.get("login_id", "ops_park")).status_code == 200


def _post(client, path: str, status: int = 200, **kw):
    res = client.post(path, **kw)
    assert res.status_code == status, res.text
    return res.json()


def _dispatched(client, engine, password_hash, partners, **extra) -> tuple[dict, str]:
    """결제 → 상품 준비 → 택배 접수까지. (주문, 택배 접수 번호)"""
    order = _place_order(client, partners, **extra)
    _admin(client, engine, password_hash)
    _post(client, f"/admin/deliveries/{order['id']}/advance")
    body = _post(client, f"/admin/deliveries/{order['id']}/dispatch")
    return order, body["courier_shipment_id"]


def _delivered(client, engine, password_hash, partners) -> dict:
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    courier_notify(client, partners.courier.set_status(shipment_id, "DELIVERED"))
    return order


def _statuses(engine, order_id: int) -> list[tuple]:
    with engine.connect() as conn:
        return [
            (r.from_status, r.to_status, r.actor_type)
            for r in conn.execute(
                select(order_status_history)
                .where(order_status_history.c.order_id == order_id)
                .order_by(order_status_history.c.id)
            )
        ]


def _order(engine, order_id: int):
    with engine.connect() as conn:
        return conn.execute(select(orders).where(orders.c.id == order_id)).mappings().one()


# ── 결제: 결제대기 → PG 결제창 → 승인 ──────────────────────────


def test_order_starts_pending_and_returns_pg_checkout_fields(client, engine):
    address_id = _address(client)
    res = client.post(
        "/shop/orders",
        json={"product_id": 1, "shipping_address_id": address_id, "delivery_memo": "문 앞"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["order"]["status"] == "PENDING_PAYMENT"
    request = body["payment_request"]
    assert request["checkout_url"] == "http://pg.test/checkout"
    fields = request["fields"]
    assert fields["orderId"] == f"ORDER-{body['order']['id']:08d}" and fields["amount"] == "12000"
    # 구매자 정보는 PG로 (F6 Q1) — 카드번호를 받는 칸은 어디에도 없다
    assert {"customerName", "customerEmail", "customerMobilePhone"} <= fields.keys()
    # 결제 전 주문은 내역에 보이지 않는다
    assert client.get("/shop/orders").json()["items"] == []
    assert _statuses(engine, body["order"]["id"]) == [(None, "PENDING_PAYMENT", "CUSTOMER")]


def test_payment_confirm_stores_pg_result(client, engine, partners):
    order = _place_order(client, partners)
    assert order["status"] == "PAID" and order["claims"] == []
    assert order["card_number_masked"] == "123456******3452"
    assert order["approve_no"] == "12345678" and order["receipt_url"].startswith("http://pg.test/")
    assert order["expected_delivery_at"] is not None and order["tracking_no"] is None
    [(_, key, order_no, amount)] = partners.pg.calls
    assert order_no == f"ORDER-{order['id']:08d}" and amount == 12000
    with engine.connect() as conn:
        assert conn.execute(select(payment.c.pg_tid)).scalar_one() == key
    assert _statuses(engine, order["id"]) == [
        (None, "PENDING_PAYMENT", "CUSTOMER"),
        ("PENDING_PAYMENT", "PAID", "CUSTOMER"),
    ]
    # 같은 승인을 다시 보내도(새로고침) 결과는 그대로
    again = client.post(
        f"/shop/orders/{order['id']}/payment", json={"payment_key": key, "amount": 12000}
    )
    assert again.status_code == 200 and len(partners.pg.calls) == 1


def _pending(client, partners) -> tuple[dict, str]:
    address_id = _address(client)
    body = client.post(
        "/shop/orders", json={"product_id": 1, "shipping_address_id": address_id}
    ).json()
    return body["order"], partners.pg.approve(body["payment_request"]["fields"])


def test_tampered_amount_is_not_sent_to_pg(client, partners):
    order, key = _pending(client, partners)
    res = client.post(
        f"/shop/orders/{order['id']}/payment", json={"payment_key": key, "amount": 100}
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "AMOUNT_MISMATCH"
    assert partners.pg.calls == []


def test_pg_rejection_and_outage_keep_order_pending(client, engine, partners):
    order, key = _pending(client, partners)
    res = client.post(
        f"/shop/orders/{order['id']}/payment", json={"payment_key": "pay_unknown", "amount": 12000}
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "PAYMENT_FAILED"
    partners.pg.down = True
    res = client.post(
        f"/shop/orders/{order['id']}/payment", json={"payment_key": key, "amount": 12000}
    )
    assert res.status_code == 502 and res.json()["error"]["code"] == "PG_UNAVAILABLE"
    assert _order(engine, order["id"])["status"] == "PENDING_PAYMENT"


def test_unpaid_order_expires_after_30_minutes(client, engine, partners):
    order, key = _pending(client, partners)
    with engine.begin() as conn:
        conn.execute(
            update(orders)
            .where(orders.c.id == order["id"])
            .values(ordered_at=func.now() - timedelta(minutes=31))
        )
    res = client.post(
        f"/shop/orders/{order['id']}/payment", json={"payment_key": key, "amount": 12000}
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "PAYMENT_EXPIRED"
    assert partners.pg.calls == []
    assert _order(engine, order["id"])["status"] == "PAYMENT_EXPIRED"
    assert _statuses(engine, order["id"])[-1] == ("PENDING_PAYMENT", "PAYMENT_EXPIRED", "SYSTEM")


# ── 관리자: 상품 준비 → 택배 접수 → 택배사 통지 ─────────────────


def test_dispatch_sends_only_waybill_fields(client, engine, password_hash, partners):
    order, shipment_id = _dispatched(
        client, engine, password_hash, partners, delivery_memo="문 앞에 놓아 주세요"
    )
    payload = partners.courier.payloads[shipment_id]
    assert payload["orderNo"] == f"ORDER-{order['id']:08d}"
    assert payload["receiver"] == {
        "name": "홍길동",
        "phone": "010-0000-1234",
        "zipCode": "90001",
        "address": "서울특별시 가상구 가상로 1",
        "addressDetail": "101호",
    }
    assert payload["memo"] == "문 앞에 놓아 주세요"
    assert payload["items"] == [{"name": "무지 머그컵", "quantity": 1}]
    # 회원번호·이메일은 보내지 않는다 (F6 Q2)
    assert "buyer@example.com" not in str(payload) and "member" not in str(payload)
    row = _order(engine, order["id"])
    assert row["status"] == "PREPARING" and row["courier_status"] == "RECEIVED"
    # 같은 주문을 두 번 접수하지 않는다
    res = client.post(f"/admin/deliveries/{order['id']}/dispatch")
    assert res.status_code == 409 and res.json()["error"]["code"] == "ALREADY_DISPATCHED"


def test_courier_notifications_move_order_to_shipping_and_delivered(
    client, engine, password_hash, partners
):
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    res = courier_notify(client, partners.courier.set_status(shipment_id, "PICKED_UP"))
    assert res.status_code == 200 and res.json()["result"] == "applied"
    [mine] = client.get("/shop/orders").json()["items"]
    assert mine["status"] == "SHIPPING" and mine["courier"] == "데모택배(가상)"
    assert mine["tracking_no"].startswith("6") and mine["courier_status"] == "PICKED_UP"

    courier_notify(client, partners.courier.set_status(shipment_id, "OUT_FOR_DELIVERY"))
    courier_notify(client, partners.courier.set_status(shipment_id, "DELIVERED"))
    row = _order(engine, order["id"])
    assert row["status"] == "DELIVERED" and row["delivered_at"] is not None
    assert _statuses(engine, order["id"])[-2:] == [
        ("PREPARING", "SHIPPING", "SYSTEM"),
        ("SHIPPING", "DELIVERED", "SYSTEM"),
    ]
    # 늦게 도착한 옛 통지는 반영하지 않는다
    stale = dict(partners.courier.shipments[shipment_id], status="IN_TRANSIT")
    assert courier_notify(client, stale).json()["result"] == "stale"
    assert _order(engine, order["id"])["courier_status"] == "DELIVERED"


def test_courier_jump_to_delivered_records_both_steps(client, engine, password_hash, partners):
    """택배사 DB에서 바로 배송완료로 바꾼 경우 — 배송중·배송완료를 차례로 남긴다"""
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    courier_notify(client, partners.courier.set_status(shipment_id, "DELIVERED"))
    assert [s[1] for s in _statuses(engine, order["id"])][-2:] == ["SHIPPING", "DELIVERED"]


@pytest.mark.parametrize("case", ["bad_signature", "stale_timestamp"])
def test_courier_notification_requires_valid_signature(
    client, engine, password_hash, partners, case
):
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    shipment = partners.courier.set_status(shipment_id, "DELIVERED")
    if case == "bad_signature":
        res = courier_notify(client, shipment, secret="wrong-secret-wrong-secret-wrong-secret")
    else:
        res = courier_notify(client, shipment, timestamp=1)
    assert res.status_code == 401
    assert _order(engine, order["id"])["status"] == "PREPARING"


def test_unknown_shipment_notification_is_acknowledged(client):
    res = courier_notify(
        client, {"shipmentId": "SHPNOPE", "kind": "DELIVERY", "status": "DELIVERED"}
    )
    assert res.status_code == 200 and res.json()["result"] == "unknown"


def test_sync_pulls_status_when_notification_was_missed(client, engine, password_hash, partners):
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    partners.courier.set_status(shipment_id, "IN_TRANSIT")  # 통지가 오지 않음
    body = _post(client, f"/admin/deliveries/{order['id']}/sync")
    assert body["status"] == "SHIPPING" and body["results"] == ["applied"]


def test_dispatch_is_an_order_update_access_log(client, engine, password_hash, partners):
    order, _ = _dispatched(client, engine, password_hash, partners)
    event = outbox_payloads(engine)[-1]
    assert (event["action"], event["data_category"], event["result"]) == (
        "UPDATE",
        "ORDER",
        "SUCCESS",
    )
    assert event["subject"]["ids"] == [str(_order(engine, order["id"])["member_id"])]


def test_courier_outage_on_dispatch_is_502(client, engine, password_hash, partners):
    order = _place_order(client, partners)
    _admin(client, engine, password_hash)
    _post(client, f"/admin/deliveries/{order['id']}/advance")
    partners.courier.down = True
    res = client.post(f"/admin/deliveries/{order['id']}/dispatch")
    assert res.status_code == 502 and res.json()["error"]["code"] == "COURIER_UNAVAILABLE"
    assert _order(engine, order["id"])["courier_shipment_id"] is None


def test_legacy_shipping_order_can_be_completed_by_admin(client, engine, password_hash, partners):
    """연동 이전 주문(택배 접수 없이 배송중)만 관리자가 배송완료로 넘긴다"""
    order = _place_order(client, partners)
    with engine.begin() as conn:
        conn.execute(
            update(orders)
            .where(orders.c.id == order["id"])
            .values(status="SHIPPING", courier="데모택배(가상)", tracking_no="DEMO0000000001")
        )
    _admin(client, engine, password_hash)
    assert _post(client, f"/admin/deliveries/{order['id']}/advance")["status"] == "DELIVERED"


def test_admin_cannot_skip_courier_for_tracked_delivery(client, engine, password_hash, partners):
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    courier_notify(client, partners.courier.set_status(shipment_id, "PICKED_UP"))
    res = client.post(f"/admin/deliveries/{order['id']}/advance")
    assert res.status_code == 409


def test_marketing_cannot_manage_deliveries(client, engine, password_hash, partners):
    _place_order(client, partners)
    _admin(client, engine, password_hash, login_id="mkt_lee", team="MARKETING", role="MARKETING")
    assert client.get("/admin/deliveries").status_code == 403


def test_delivery_list_masks_recipient_and_search_logs_key_names_only(
    client, engine, password_hash, partners
):
    order = _place_order(client, partners)
    _pending(client, partners)  # 결제대기 주문은 배송 목록에 없다 (다른 회원)
    _admin(client, engine, password_hash)

    [item] = client.get("/admin/deliveries").json()["items"]
    assert item["ship_recipient"] == "홍*동"
    assert item["ship_phone"] == "010-****-1234"
    assert item["ship_address"] == "서울특별시 가상구 …"

    res = client.post("/admin/deliveries/search", json={"recipient": "길동", "status": "PAID"})
    assert [i["id"] for i in res.json()["items"]] == [order["id"]]
    event = outbox_payloads(engine)[-1]
    assert event["request"]["query_keys"] == ["recipient", "status"]
    assert "길동" not in str(event)

    # 상세에서는 전체 배송 정보와 PG 승인 결과
    detail = client.get(f"/admin/orders/{order['id']}").json()
    assert detail["ship_recipient"] == "홍길동"
    assert detail["card_number_masked"] == "123456******3452"
    assert [h["to_status"] for h in detail["status_history"]] == ["PENDING_PAYMENT", "PAID"]


# ── 고객: 취소 ──────────────────────────────────────────


def test_customer_cancel_paid_order_cancels_pg_payment(client, engine, partners):
    order = _place_order(client, partners)
    body = _post(client, f"/shop/orders/{order['id']}/cancel", json={"reason_code": "WRONG_ORDER"})
    assert body["status"] == "CANCELLED"
    [claim] = body["claims"]
    assert claim["claim_type"] == "CANCEL" and claim["status"] == "COMPLETED"
    assert claim["refund_amount"] == 12000
    assert partners.pg.calls[-1][0] == "cancel"
    with engine.connect() as conn:
        tid = conn.execute(select(order_claim.c.refund_pg_tid)).scalar_one()
    assert tid.startswith("ctx_")
    assert _statuses(engine, order["id"])[-1] == ("PAID", "CANCELLED", "CUSTOMER")


def test_cancel_dispatched_order_cancels_courier_first(client, engine, password_hash, partners):
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    body = _post(client, f"/shop/orders/{order['id']}/cancel", json={"reason_code": "DELAY"})
    assert body["status"] == "CANCELLED"
    assert partners.courier.shipments[shipment_id]["status"] == "CANCELED"
    assert _order(engine, order["id"])["courier_status"] == "CANCELED"


def test_cannot_cancel_once_courier_picked_up(client, engine, password_hash, partners):
    """택배사가 집하했지만 통지가 아직 오지 않은 경우 — 택배 접수 취소가 거절된다"""
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    partners.courier.set_status(shipment_id, "PICKED_UP")
    res = client.post(f"/shop/orders/{order['id']}/cancel", json={"reason_code": "CHANGE_OF_MIND"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "ALREADY_SHIPPED"
    assert partners.pg.calls[-1][0] == "confirm"  # 결제는 취소하지 않았다


def test_cannot_cancel_after_shipping(client, engine, password_hash, partners):
    order, shipment_id = _dispatched(client, engine, password_hash, partners)
    courier_notify(client, partners.courier.set_status(shipment_id, "PICKED_UP"))
    res = client.post(f"/shop/orders/{order['id']}/cancel", json={"reason_code": "CHANGE_OF_MIND"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "INVALID_ORDER_STATUS"


def test_cannot_cancel_another_members_order(client, partners):
    order = _place_order(client, partners, email="other@example.com")
    signup(client)
    res = client.post(f"/shop/orders/{order['id']}/cancel", json={"reason_code": "OTHER"})
    assert res.status_code == 404


def test_legacy_payment_cancel_is_recorded_locally(client, engine, partners):
    """연동 이전 결제(PG에 기록 없음)는 쇼핑몰 안에서만 취소 기록"""
    order = _place_order(client, partners)
    with engine.begin() as conn:
        conn.execute(update(payment).values(pg_tid="MOCKPG-SEED0000000001"))
    _post(client, f"/shop/orders/{order['id']}/cancel", json={"reason_code": "OTHER"})
    assert partners.pg.calls[-1][0] == "confirm"
    with engine.connect() as conn:
        tid = conn.execute(select(order_claim.c.refund_pg_tid)).scalar_one()
    assert tid.startswith("MOCKPG-CANCEL-")


# ── 고객: 청약철회 → 택배 회수 → 관리자 회수 확인 ─────────────────


def test_return_pickup_then_admin_completes_after_arrival(client, engine, password_hash, partners):
    order = _place_order(client, partners)
    early = client.post(f"/shop/orders/{order['id']}/return", json={"reason_code": "DEFECT"})
    assert early.status_code == 409  # 배송완료 전에는 청약철회가 아니라 취소

    _admin(client, engine, password_hash)
    _post(client, f"/admin/deliveries/{order['id']}/advance")
    shipment_id = _post(client, f"/admin/deliveries/{order['id']}/dispatch")["courier_shipment_id"]
    courier_notify(client, partners.courier.set_status(shipment_id, "DELIVERED"))

    body = _post(
        client,
        f"/shop/orders/{order['id']}/return",
        json={"reason_code": "DEFECT", "reason_detail": "흠집이 있어요"},
    )
    assert body["status"] == "RETURN_REQUESTED" and body["return_deadline"] is not None
    [claim] = body["claims"]
    assert claim["pickup_status"] == "RETURN_RECEIVED"
    with engine.connect() as conn:
        pickup_id = conn.execute(select(order_claim.c.pickup_shipment_id)).scalar_one()
    pickup = partners.courier.payloads[pickup_id]
    assert pickup["pickup"]["name"] == "홍길동" and pickup["returnTo"]["name"] == "데모샵(가상)"

    res = client.post(f"/admin/deliveries/{order['id']}/return-complete")
    assert res.status_code == 409 and res.json()["error"]["code"] == "RETURN_NOT_ARRIVED"

    courier_notify(client, partners.courier.set_status(pickup_id, "RETURN_PICKED_UP"))
    courier_notify(client, partners.courier.set_status(pickup_id, "RETURN_DELIVERED"))
    done = _post(client, f"/admin/deliveries/{order['id']}/return-complete")
    assert done["status"] == "RETURNED"
    [claim] = client.get("/shop/orders").json()["items"][0]["claims"]
    assert claim["status"] == "COMPLETED" and claim["refund_amount"] == 12000
    assert claim["pickup_tracking_no"] is not None
    assert partners.pg.calls[-1][0] == "cancel"
    assert _statuses(engine, order["id"])[-1] == ("RETURN_REQUESTED", "RETURNED", "OPERATOR")


def test_return_period_expires_after_seven_days(client, engine, password_hash, partners):
    order = _delivered(client, engine, password_hash, partners)
    with engine.begin() as conn:
        conn.execute(
            update(orders)
            .where(orders.c.id == order["id"])
            .values(delivered_at=func.now() - timedelta(days=8))
        )
    res = client.post(f"/shop/orders/{order['id']}/return", json={"reason_code": "CHANGE_OF_MIND"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "WITHDRAWAL_PERIOD_EXPIRED"


def test_return_complete_requires_a_request(client, engine, password_hash, partners):
    order = _place_order(client, partners)
    _admin(client, engine, password_hash)
    res = client.post(f"/admin/deliveries/{order['id']}/return-complete")
    assert res.status_code == 409
    event = outbox_payloads(engine)[-1]
    assert event["result"] == "FAILURE"


# ── 탈퇴 ────────────────────────────────────────────────


def test_withdrawal_waits_for_orders_in_progress(client, partners):
    _place_order(client, partners)
    res = client.post("/shop/me/withdraw", json={"password": "customer-pass-1234"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "ORDERS_IN_PROGRESS"


def test_withdrawal_expires_unpaid_orders(client, engine, partners):
    order, _ = _pending(client, partners)
    res = client.post("/shop/me/withdraw", json={"password": "customer-pass-1234"})
    assert res.status_code == 204
    assert _order(engine, order["id"])["status"] == "PAYMENT_EXPIRED"


def test_withdrawal_retains_claims_and_clears_tracking(client, engine, partners):
    order = _place_order(client, partners, delivery_memo="경비실")
    client.post(
        f"/shop/orders/{order['id']}/cancel",
        json={"reason_code": "OTHER", "reason_detail": "개인 사정"},
    )
    res = client.post("/shop/me/withdraw", json={"password": "customer-pass-1234"})
    assert res.status_code == 204
    with engine.connect() as conn:
        detail = conn.execute(select(order_claim.c.reason_detail)).scalar_one()
        retained = conn.execute(select(retained_member_record.c.data)).scalar_one()
    assert detail is None  # 운영 테이블의 사유 상세는 비우고
    assert _order(engine, order["id"])["delivery_memo"] is None
    [kept] = retained["orders"]
    assert kept["status"] == "CANCELLED" and kept["card_number_masked"] == "123456******3452"
    assert kept["shipping"]["delivery_memo"] == "경비실"
    assert kept["claims"][0]["reason_detail"] == "개인 사정"  # 분리보관으로 옮긴다


@pytest.mark.parametrize(
    "update_",
    [
        {"pg_secret_key": None},
        {"courier_api_key": SecretStr("short")},
        {"courier_webhook_secret": None},
        {"pg_api_url": "file:///etc/passwd"},
    ],
)
def test_api_refuses_to_start_without_partner_settings(settings, update_):
    with pytest.raises(ValueError, match="PLATFORM_"):
        create_app(settings.model_copy(update=update_))
