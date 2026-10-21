"""배송관리·취소·청약철회 (커머스 확장, 판단기록 F3·F4)"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.models import order_claim, order_status_history, orders, retained_member_record

from conftest import add_operator, login, outbox_payloads, signup

HOME = {
    "label": "집",
    "recipient": "홍길동",  # 가상
    "phone": "010-0000-1234",
    "zip_code": "90001",
    "address": "서울특별시 가상구 가상로 1",
    "address_detail": "101호",
}


def _place_order(client, email: str = "buyer@example.com") -> dict:
    signup(client, email=email)
    res = client.post("/shop/me/addresses", json=HOME)
    address_id = max(i["id"] for i in res.json()["items"])
    res = client.post(
        "/shop/orders",
        json={"product_id": 1, "shipping_address_id": address_id, "card_company": "하늘카드"},
    )
    assert res.status_code == 201
    return res.json()


def _admin(client, engine, password_hash, **overrides):
    add_operator(engine, password_hash, **overrides)
    assert login(client, overrides.get("login_id", "ops_park")).status_code == 200


def _advance(client, order_id: int, times: int = 1):
    res = None
    for _ in range(times):
        res = client.post(f"/admin/deliveries/{order_id}/advance")
        assert res.status_code == 200, res.text
    return res


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


# ── 고객: 배송 조회 ─────────────────────────────────────


def test_new_order_shows_expected_delivery_and_history(client, engine):
    order = _place_order(client)
    assert order["status"] == "PAID" and order["claims"] == []
    assert order["tracking_no"] is None and order["return_deadline"] is None
    # 상품 1(머그컵)의 고정 소요 일수 = 1 + (1 % 3) = 2일 (마이그레이션 0011)
    assert order["expected_delivery_at"] is not None
    assert _statuses(engine, order["id"]) == [(None, "PAID", "CUSTOMER")]


# ── 관리자: 배송 단계 ─────────────────────────────────────


def test_admin_advances_delivery_step_by_step(client, engine, password_hash):
    order = _place_order(client)
    _admin(client, engine, password_hash)

    assert _advance(client, order["id"]).json()["status"] == "PREPARING"
    assert _advance(client, order["id"]).json()["status"] == "SHIPPING"
    [mine] = client.get("/shop/orders").json()["items"]
    assert mine["courier"] == "데모택배(가상)" and mine["tracking_no"].startswith("DEMO")
    assert _advance(client, order["id"]).json()["status"] == "DELIVERED"

    res = client.post(f"/admin/deliveries/{order['id']}/advance")
    assert res.status_code == 409 and res.json()["error"]["code"] == "INVALID_ORDER_STATUS"
    assert [s[1] for s in _statuses(engine, order["id"])] == [
        "PAID",
        "PREPARING",
        "SHIPPING",
        "DELIVERED",
    ]


def test_advance_is_an_order_update_access_log(client, engine, password_hash):
    order = _place_order(client)
    _admin(client, engine, password_hash)
    _advance(client, order["id"])
    event = outbox_payloads(engine)[-1]
    assert (event["action"], event["data_category"], event["result"]) == (
        "UPDATE",
        "ORDER",
        "SUCCESS",
    )
    assert event["subject"]["ids"] == [str(order_member(engine, order["id"]))]


def order_member(engine, order_id: int) -> int:
    with engine.connect() as conn:
        return conn.execute(select(orders.c.member_id).where(orders.c.id == order_id)).scalar()


def test_marketing_cannot_manage_deliveries(client, engine, password_hash):
    _place_order(client)
    _admin(client, engine, password_hash, login_id="mkt_lee", team="MARKETING", role="MARKETING")
    assert client.get("/admin/deliveries").status_code == 403


def test_delivery_list_masks_recipient_and_search_logs_key_names_only(
    client, engine, password_hash
):
    order = _place_order(client)
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

    # 상세에서는 전체 배송 정보
    detail = client.get(f"/admin/orders/{order['id']}").json()
    assert (
        detail["ship_recipient"] == "홍길동" and detail["status_history"][0]["to_status"] == "PAID"
    )


# ── 고객: 취소 ──────────────────────────────────────────


@pytest.mark.parametrize("steps", [0, 1], ids=["paid", "preparing"])
def test_customer_can_cancel_until_preparing(client, engine, password_hash, steps):
    order = _place_order(client)
    if steps:
        _admin(client, engine, password_hash)
        _advance(client, order["id"], steps)

    res = client.post(f"/shop/orders/{order['id']}/cancel", json={"reason_code": "WRONG_ORDER"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "CANCELLED"
    [claim] = body["claims"]
    assert claim["claim_type"] == "CANCEL" and claim["status"] == "COMPLETED"
    assert claim["refund_amount"] == 12000
    with engine.connect() as conn:
        tid = conn.execute(select(order_claim.c.refund_pg_tid)).scalar_one()
    assert tid.startswith("MOCKPG-CANCEL-")
    from_status = "PREPARING" if steps else "PAID"
    assert _statuses(engine, order["id"])[-1] == (from_status, "CANCELLED", "CUSTOMER")


def test_cannot_cancel_after_shipping(client, engine, password_hash):
    order = _place_order(client)
    _admin(client, engine, password_hash)
    _advance(client, order["id"], 2)
    res = client.post(f"/shop/orders/{order['id']}/cancel", json={"reason_code": "CHANGE_OF_MIND"})
    assert res.status_code == 409


def test_cannot_cancel_another_members_order(client):
    order = _place_order(client, email="other@example.com")
    signup(client)
    res = client.post(f"/shop/orders/{order['id']}/cancel", json={"reason_code": "OTHER"})
    assert res.status_code == 404


# ── 고객: 청약철회 → 관리자 회수 확인 ─────────────────────────


def test_return_within_seven_days_then_admin_completes(client, engine, password_hash):
    order = _place_order(client)
    _admin(client, engine, password_hash)

    early = client.post(f"/shop/orders/{order['id']}/return", json={"reason_code": "DEFECT"})
    assert early.status_code == 409  # 배송완료 전에는 청약철회가 아니라 취소

    _advance(client, order["id"], 3)
    res = client.post(
        f"/shop/orders/{order['id']}/return",
        json={"reason_code": "DEFECT", "reason_detail": "흠집이 있어요"},
    )
    assert res.status_code == 200 and res.json()["status"] == "RETURN_REQUESTED"
    assert res.json()["return_deadline"] is not None

    done = client.post(f"/admin/deliveries/{order['id']}/return-complete")
    assert done.status_code == 200 and done.json()["status"] == "RETURNED"
    [claim] = client.get("/shop/orders").json()["items"][0]["claims"]
    assert claim["status"] == "COMPLETED" and claim["refund_amount"] == 12000
    assert _statuses(engine, order["id"])[-1] == ("RETURN_REQUESTED", "RETURNED", "OPERATOR")


def test_return_period_expires_after_seven_days(client, engine, password_hash):
    order = _place_order(client)
    _admin(client, engine, password_hash)
    _advance(client, order["id"], 3)
    with engine.begin() as conn:
        conn.execute(
            update(orders)
            .where(orders.c.id == order["id"])
            .values(delivered_at=func.now() - timedelta(days=8))
        )
    res = client.post(f"/shop/orders/{order['id']}/return", json={"reason_code": "CHANGE_OF_MIND"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "WITHDRAWAL_PERIOD_EXPIRED"


def test_return_complete_requires_a_request(client, engine, password_hash):
    order = _place_order(client)
    _admin(client, engine, password_hash)
    res = client.post(f"/admin/deliveries/{order['id']}/return-complete")
    assert res.status_code == 409
    event = outbox_payloads(engine)[-1]
    assert event["result"] == "FAILURE"


# ── 탈퇴 ────────────────────────────────────────────────


def test_withdrawal_waits_for_orders_in_progress(client):
    _place_order(client)
    res = client.post("/shop/me/withdraw", json={"password": "customer-pass-1234"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "ORDERS_IN_PROGRESS"


def test_withdrawal_retains_claims_and_clears_tracking(client, engine, password_hash):
    order = _place_order(client)
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
    [kept] = retained["orders"]
    assert kept["status"] == "CANCELLED"
    assert kept["claims"][0]["reason_detail"] == "개인 사정"  # 분리보관으로 옮긴다
