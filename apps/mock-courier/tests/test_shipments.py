"""가상 택배 — 접수·조회·취소·반품 회수, 시간 기반 진행, DB 직접 변경 반영, 쇼핑몰 통지"""

import hashlib
import hmac

from sqlalchemy import text

from conftest import CUSTOMER, SHOP, WEBHOOK_SECRET, age, dispatch, headers


def _get(client, shipment_id: str) -> dict:
    res = client.get(f"/v1/shipments/{shipment_id}", headers=headers())
    assert res.status_code == 200, res.text
    return res.json()


def test_dispatch_requires_api_key(client):
    res = client.post("/v1/shipments", json={}, headers=headers("wrong"))
    assert res.status_code == 401
    res = client.get("/v1/shipments/SHP0", headers={})
    assert res.status_code == 401


def test_dispatch_creates_received_shipment_without_tracking_no(client):
    res = dispatch(client)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "RECEIVED" and body["kind"] == "DELIVERY"
    assert body["trackingNo"] is None and body["courier"] == "데모택배(가상)"
    # 받는 사람 정보는 응답으로 되돌려주지 않는다
    assert CUSTOMER["name"] not in res.text


def test_dispatch_is_idempotent_per_order(client):
    first = dispatch(client).json()
    again = dispatch(client)
    assert again.status_code == 200 and again.json()["shipmentId"] == first["shipmentId"]


def test_dispatch_rejects_missing_receiver_fields(client):
    res = dispatch(client, receiver={**CUSTOMER, "address": ""})
    assert res.status_code == 400 and res.json()["code"] == "INVALID_REQUEST"


def test_auto_progress_follows_settings_and_issues_tracking_no(client, app):
    shipment = dispatch(client).json()
    app.state.tick()
    assert _get(client, shipment["shipmentId"])["status"] == "RECEIVED"  # 아직 60초 안 됨

    age(app, shipment["shipmentId"], 61)
    app.state.tick()
    picked = _get(client, shipment["shipmentId"])
    assert picked["status"] == "PICKED_UP" and picked["trackingNo"].startswith("6")
    assert picked["pickedUpAt"] is not None

    for expected in ("IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED"):
        age(app, shipment["shipmentId"], 31)
        app.state.tick()
        assert _get(client, shipment["shipmentId"])["status"] == expected
    done = _get(client, shipment["shipmentId"])
    assert done["deliveredAt"] is not None
    assert [e["status"] for e in done["events"]] == [
        "RECEIVED",
        "PICKED_UP",
        "IN_TRANSIT",
        "OUT_FOR_DELIVERY",
        "DELIVERED",
    ]


def test_changed_delay_setting_applies(client, app):
    shipment = dispatch(client).json()
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE setting SET value = '5' WHERE key = 'delay.RECEIVED'"))
    try:
        age(app, shipment["shipmentId"], 6)
        app.state.tick()
        assert _get(client, shipment["shipmentId"])["status"] == "PICKED_UP"
    finally:
        with app.state.engine.begin() as conn:
            conn.execute(text("UPDATE setting SET value = '60' WHERE key = 'delay.RECEIVED'"))


def test_auto_progress_off_keeps_status(client, app):
    shipment = dispatch(client).json()
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE setting SET value = 'off' WHERE key = 'auto_progress'"))
    age(app, shipment["shipmentId"], 3600)
    app.state.tick()
    assert _get(client, shipment["shipmentId"])["status"] == "RECEIVED"


def test_direct_db_change_is_completed_by_trigger_and_notified(client, app, outbox):
    """DB에서 status만 바꿔도 송장번호·시각·이력이 맞춰지고 쇼핑몰로 통지된다"""
    shipment = dispatch(client).json()
    with app.state.engine.begin() as conn:
        conn.execute(
            text("UPDATE shipment SET status = 'DELIVERED' WHERE shipment_id = :id"),
            {"id": shipment["shipmentId"]},
        )
        source = conn.execute(
            text("SELECT source FROM shipment_event WHERE status = 'DELIVERED'")
        ).scalar_one()
    assert source == "DB"
    app.state.tick()
    [notified] = outbox.shipments()
    assert notified["status"] == "DELIVERED" and notified["trackingNo"] is not None
    assert notified["pickedUpAt"] and notified["deliveredAt"]
    # 다음 루프에서는 다시 보내지 않는다
    app.state.tick()
    assert len(outbox.sent) == 1


def test_db_rejects_unknown_status(client, app):
    shipment = dispatch(client).json()
    with app.state.engine.connect() as conn:
        try:
            conn.execute(
                text("UPDATE shipment SET status = 'LOST' WHERE shipment_id = :id"),
                {"id": shipment["shipmentId"]},
            )
            raise AssertionError("CHECK constraint should reject unknown status")
        except Exception as error:  # noqa: BLE001
            assert "shipment_status_check" in str(error)


def test_notification_is_signed_and_carries_no_receiver_info(client, app, outbox):
    shipment = dispatch(client).json()
    age(app, shipment["shipmentId"], 61)
    app.state.tick()
    [(sent_headers, body)] = outbox.sent
    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        sent_headers["X-Courier-Timestamp"].encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    assert sent_headers["X-Courier-Signature"] == "v1=" + expected
    assert CUSTOMER["name"].encode() not in body and CUSTOMER["phone"].encode() not in body


def test_failed_notification_is_retried_later(client, app, outbox):
    shipment = dispatch(client).json()
    age(app, shipment["shipmentId"], 61)
    outbox.status = 503
    app.state.tick()
    assert len(outbox.sent) == 1
    app.state.tick()  # 재시도 시각 전 — 보내지 않음
    assert len(outbox.sent) == 1
    outbox.status = 200
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE shipment SET next_notify_at = now()"))
    app.state.tick()
    assert len(outbox.sent) == 2
    with app.state.engine.connect() as conn:
        assert conn.execute(text("SELECT notified_status FROM shipment")).scalar_one() == (
            "PICKED_UP"
        )


def test_cancel_only_before_pickup(client, app, outbox):
    first = dispatch(client).json()
    res = client.post(f"/v1/shipments/{first['shipmentId']}/cancel", headers=headers())
    assert res.status_code == 200 and res.json()["status"] == "CANCELED"
    app.state.tick()
    assert outbox.sent == []  # 쇼핑몰이 요청한 취소는 통지하지 않음

    second = dispatch(client, order_no="ORDER-00000002").json()
    age(app, second["shipmentId"], 61)
    app.state.tick()
    res = client.post(f"/v1/shipments/{second['shipmentId']}/cancel", headers=headers())
    assert res.status_code == 409 and res.json()["code"] == "ALREADY_PICKED_UP"


def test_return_pickup_flow(client, app, outbox):
    res = client.post(
        "/v1/returns",
        json={
            "orderNo": "ORDER-00000001",
            "pickup": CUSTOMER,
            "returnTo": SHOP,
            "originalTrackingNo": "600000000001",
            "items": [{"name": "머그컵", "quantity": 1}],
        },
        headers=headers(),
    )
    assert res.status_code == 201, res.text
    ret = res.json()
    assert ret["kind"] == "RETURN" and ret["status"] == "RETURN_RECEIVED"
    # 같은 주문의 배송 접수와 반품 회수는 따로 존재할 수 있다
    assert dispatch(client).status_code == 201

    for expected, wait in (
        ("RETURN_PICKED_UP", 61),
        ("RETURN_IN_TRANSIT", 31),
        ("RETURN_DELIVERED", 31),
    ):
        age(app, ret["shipmentId"], wait)
        app.state.tick()
        assert _get(client, ret["shipmentId"])["status"] == expected
    assert outbox.shipments()[-1]["status"] == "RETURN_DELIVERED"
