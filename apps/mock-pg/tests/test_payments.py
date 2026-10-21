"""가상 PG — 결제창·승인·조회·취소"""

from urllib.parse import parse_qs, urlsplit

from sqlalchemy import text

from conftest import CARD, SECRET_KEY, SHOP, auth, open_checkout, pay, payment_key_from


def _confirm(client, key: str, order_id: str = "ORDER-00000001", amount: int = 15000, **kw):
    return client.post(
        "/v1/payments/confirm",
        json={"paymentKey": key, "orderId": order_id, "amount": amount},
        headers=kw.get("headers", auth()),
    )


def test_checkout_page_shows_order_and_warning(client):
    res = open_checkout(client)
    assert res.status_code == 200
    assert "머그컵" in res.text and "15,000원" in res.text
    assert "가상 결제 — 실제 카드번호 입력 금지" in res.text
    # 스크립트 없이 동작, 결제 후 이동은 허용한 쇼핑몰로만
    csp = res.headers["content-security-policy"]
    assert "default-src 'none'" in csp and f"form-action 'self' {SHOP}" in csp


def test_checkout_rejects_unknown_client_key_and_foreign_return_url(client):
    assert open_checkout(client, clientKey="wrong").status_code == 400
    res = open_checkout(client, successUrl="https://evil.example/steal")
    assert res.status_code == 400 and "허용되지 않은" in res.text


def test_card_approval_redirects_to_shop_with_payment_key(client):
    res = open_checkout(client)
    key = payment_key_from(res.text)
    res = client.post(
        f"/checkout/{key}/approve",
        data={"card_company": "바다카드", "card_number": "1234-5678-9012-3452", "installment": "0"},
    )
    assert res.status_code == 303
    location = urlsplit(res.headers["location"])
    assert f"{location.scheme}://{location.netloc}" == SHOP
    query = parse_qs(location.query)
    assert query["step"] == ["ok"]  # 쇼핑몰이 붙인 값은 그대로
    assert query["paymentKey"] == [key] and query["amount"] == ["15000"]


def test_full_card_number_is_never_stored(client, app):
    key = pay(client)
    with app.state.engine.connect() as conn:
        dump = str(conn.execute(text("SELECT * FROM payment")).all())
    assert CARD not in dump
    assert "123456******3452" in dump
    assert key in dump


def test_invalid_card_number_shows_error_and_stays_ready(client):
    key = payment_key_from(open_checkout(client).text)
    res = client.post(
        f"/checkout/{key}/approve", data={"card_company": "하늘카드", "card_number": "12"}
    )
    assert res.status_code == 400 and "카드번호를 확인" in res.text
    assert client.get(f"/v1/payments/{key}", headers=auth()).json()["status"] == "READY"


def test_abort_returns_to_fail_url(client):
    key = payment_key_from(open_checkout(client).text)
    res = client.post(f"/checkout/{key}/abort")
    assert res.status_code == 303
    query = parse_qs(urlsplit(res.headers["location"]).query)
    assert query["code"] == ["PAY_PROCESS_CANCELED"] and query["step"] == ["fail"]
    assert client.get(f"/v1/payments/{key}", headers=auth()).json()["status"] == "ABORTED"


def test_confirm_returns_masked_card_and_approval(client):
    key = pay(client)
    res = _confirm(client, key)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "DONE" and body["totalAmount"] == 15000
    assert body["card"]["number"] == "123456******3452"
    assert body["card"]["installmentPlanMonths"] == 3
    assert len(body["card"]["approveNo"]) == 8
    assert body["receipt"]["url"] == f"http://pg.test/receipt/{key}"
    # 같은 승인을 다시 요청하면 결과를 그대로 (네트워크 재시도 대비)
    again = _confirm(client, key).json()
    assert again["card"]["approveNo"] == body["card"]["approveNo"]


def test_confirm_requires_secret_key(client):
    key = pay(client)
    assert _confirm(client, key, headers={}).status_code == 401
    assert _confirm(client, key, headers=auth(SECRET_KEY + "x")).status_code == 401


def test_confirm_rejects_tampered_amount_or_order(client):
    key = pay(client)
    res = _confirm(client, key, amount=100)
    assert res.status_code == 400 and res.json()["code"] == "INVALID_REQUEST"
    assert _confirm(client, key, order_id="ORDER-99999999").status_code == 400


def test_confirm_before_card_entry_is_rejected(client):
    key = payment_key_from(open_checkout(client).text)
    res = _confirm(client, key)
    assert res.status_code == 400 and res.json()["code"] == "NOT_AUTHENTICATED_PAYMENT"


def test_stale_payment_expires(client, app):
    key = pay(client)
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE payment SET requested_at = now() - interval '11 minutes'"))
    res = _confirm(client, key)
    assert res.status_code == 400 and res.json()["code"] == "EXPIRED_PAYMENT"


def test_cancel_and_lookup(client):
    key = pay(client)
    _confirm(client, key)
    res = client.post(
        f"/v1/payments/{key}/cancel", json={"cancelReason": "고객 변심"}, headers=auth()
    )
    assert res.status_code == 200
    [cancel] = res.json()["cancels"]
    assert cancel["transactionKey"].startswith("ctx_") and cancel["cancelAmount"] == 15000
    again = client.post(f"/v1/payments/{key}/cancel", json={"cancelReason": "x"}, headers=auth())
    assert again.status_code == 409 and again.json()["code"] == "ALREADY_CANCELED_PAYMENT"
    by_order = client.get("/v1/payments/orders/ORDER-00000001", headers=auth()).json()
    assert by_order["status"] == "CANCELED" and by_order["paymentKey"] == key


def test_cannot_cancel_unapproved_payment(client):
    key = pay(client)
    res = client.post(f"/v1/payments/{key}/cancel", json={"cancelReason": "x"}, headers=auth())
    assert res.status_code == 400 and res.json()["code"] == "NOT_CANCELABLE_PAYMENT"


def test_receipt_shows_masked_number_only(client):
    key = pay(client)
    assert client.get(f"/receipt/{key}").status_code == 404  # 승인 전에는 영수증 없음
    _confirm(client, key)
    res = client.get(f"/receipt/{key}")
    assert res.status_code == 200
    assert "123456******3452" in res.text and CARD not in res.text
    assert "홍길동" not in res.text
