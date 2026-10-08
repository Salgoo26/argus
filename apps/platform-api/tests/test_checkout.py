"""결제·주문 보강 (기능 레이어 7-4 ③) — 배송지 선택·스냅샷, 카드번호 미수신, 관리자 주문 상세

카드번호는 가상 PG 결제창(브라우저)에서 입력만 받고 platform-api로 보내지 않는다.
화면이 잘못 보내더라도 서버의 응답·로그·DB·Argus 전송(outbox) 어디에도 남지 않아야 한다.
"""

import logging

import pytest
from sqlalchemy import inspect, text, update

from app.models import SHIP_COLUMNS, shipping_address

from conftest import (
    CUSTOMER_PASSWORD,
    add_operator,
    login,
    outbox_payloads,
    shop_login,
    signup,
)

# 가상 카드번호 (실존하지 않는 번호)
CARD = "1234567890123452"
CARD_FORMS = (CARD, "1234-5678-9012-3452", "1234 5678 9012 3452")
HOME = {
    "label": "집",
    "recipient": "받는사람",  # 가상
    "phone": "010-0000-1234",
    "zip_code": "90001",
    "address": "서울특별시 가상구 가상로 1",
    "address_detail": "101호",
}


def _add_address(client, **overrides) -> int:
    res = client.post("/shop/me/addresses", json={**HOME, **overrides})
    assert res.status_code == 201
    return max(i["id"] for i in res.json()["items"])


def _order(client, address_id: int | None, **extra):
    body = {"product_id": 1, "card_company": "하늘카드", **extra}
    if address_id is not None:
        body["shipping_address_id"] = address_id
    return client.post("/shop/orders", json=body)


# ── 주문: 로그인·배송지 ─────────────────────────────────────


def test_order_requires_login_even_with_address_id(client):
    res = _order(client, 1)
    assert res.status_code == 401


@pytest.mark.parametrize("address_id", [None, 999], ids=["missing", "unknown"])
def test_order_requires_one_of_my_addresses(client, address_id):
    signup(client)
    res = _order(client, address_id)
    assert res.status_code == 400
    if address_id is not None:
        assert res.json()["error"]["code"] == "SHIPPING_ADDRESS_REQUIRED"
    assert client.get("/shop/orders").json()["items"] == []


def test_cannot_ship_to_another_members_address(client):
    signup(client, email="other@example.com")
    others = _add_address(client)
    signup(client)
    res = _order(client, others)
    assert res.status_code == 400 and res.json()["error"]["code"] == "SHIPPING_ADDRESS_REQUIRED"


def test_incomplete_migrated_address_must_be_completed(client, engine):
    """예전 회원 주소에서 옮겨 온 배송지(0007)는 연락처·우편번호가 없을 수 있다 → 보완 후 주문"""
    signup(client)
    address_id = _add_address(client)
    with engine.begin() as conn:
        conn.execute(
            update(shipping_address)
            .where(shipping_address.c.id == address_id)
            .values(phone=None, zip_code=None)
        )
    res = _order(client, address_id)
    assert res.status_code == 400 and res.json()["error"]["code"] == "SHIPPING_ADDRESS_INCOMPLETE"


def test_order_keeps_a_snapshot_of_the_address(client):
    signup(client)
    address_id = _add_address(client)
    order = _order(client, address_id).json()
    assert (order["ship_recipient"], order["ship_zip_code"]) == ("받는사람", "90001")

    # 배송지를 고치고 지워도 주문의 배송 정보는 주문 시점 그대로
    client.put(f"/shop/me/addresses/{address_id}", json={**HOME, "address": "바뀐 주소"})
    client.delete(f"/shop/me/addresses/{address_id}")
    [mine] = client.get("/shop/orders").json()["items"]
    assert mine["ship_address"] == "서울특별시 가상구 가상로 1"
    assert mine["ship_address_detail"] == "101호" and mine["ship_phone"] == "010-0000-1234"


# ── 카드번호: 서버 어디에도 없다 ─────────────────────────────


def _platform_db_text(engine) -> str:
    """플랫폼 DB 전체 테이블의 모든 행을 문자열로 — 카드번호가 어딘가에 들어갔는지 찾는다"""
    chunks = []
    with engine.connect() as conn:
        for table in inspect(engine).get_table_names():
            rows = conn.execute(text(f'SELECT * FROM "{table}"')).all()  # noqa: S608 — 카탈로그 이름
            chunks.append(repr(rows))
    return "\n".join(chunks)


def test_card_number_is_never_stored_logged_or_sent(client, engine, password_hash, caplog, capsys):
    """화면이 실수로 카드번호를 보내도(절대 보내지 않게 만들었지만) 서버에는 흔적이 없다"""
    caplog.set_level(logging.DEBUG)
    signup(client)
    address_id = _add_address(client)
    responses = [
        _order(client, address_id, card_number=form, card_expiry="12/30", cvc="123")
        for form in CARD_FORMS
    ]
    responses.append(client.get("/shop/orders"))
    assert [r.status_code for r in responses[:-1]] == [201] * len(CARD_FORMS)

    # 관리자가 주문을 열람 → Argus로 갈 접속기록(outbox)이 생긴다
    client.cookies.clear()
    add_operator(engine, password_hash)
    assert login(client).status_code == 200
    order_id = responses[0].json()["id"]
    responses.append(client.get(f"/admin/orders/{order_id}"))
    responses.append(client.get("/admin/orders"))
    assert outbox_payloads(engine)  # 확인 대상이 실제로 있다

    captured = capsys.readouterr()
    places = {
        "응답": "\n".join(r.text for r in responses),
        "서버 로그": caplog.text + captured.out + captured.err,
        "플랫폼 DB": _platform_db_text(engine),
        "Argus 전송(outbox)": repr(outbox_payloads(engine)),
    }
    for place, content in places.items():
        for form in CARD_FORMS:
            assert form not in content, f"카드번호가 {place}에 있다"
    # 결제 기록에는 카드사·거래번호·승인 시각·금액만 (카드번호·끝 4자리 컬럼 없음)
    columns = {c["name"] for c in inspect(engine).get_columns("payment")}
    assert columns == {
        "id",
        "order_id",
        "method",
        "card_company",
        "pg_tid",
        "amount",
        "approved_at",
    }


# ── 관리자: 주문 상세 (배송 정보) ───────────────────────────


@pytest.fixture
def admin_with_order(client, engine, password_hash):
    signup(client)
    _order(client, _add_address(client))
    client.cookies.clear()
    add_operator(engine, password_hash)
    assert login(client).status_code == 200
    return client


def test_admin_order_detail_shows_shipping_and_is_logged(admin_with_order, engine):
    res = admin_with_order.get("/admin/orders/1")

    assert res.status_code == 200
    body = res.json()
    assert body["ship_recipient"] == "받는사람" and body["ship_address"].endswith("가상로 1")
    assert body["card_company"] == "하늘카드" and body["pg_tid"].startswith("MOCKPG-")
    [event] = [p for p in outbox_payloads(engine) if p["action"] == "READ"]
    # 주문 조회 접속기록 — READ·ORDER, 정보주체는 그 주문의 회원 PK만 (CLAUDE.md 3절 #3)
    assert event["data_category"] == "ORDER" and event["result"] == "SUCCESS"
    assert event["request"]["path"] == "/admin/orders/{order_id}"
    assert event["subject"]["ids"] == ["1"]
    assert "가상로" not in str(event) and "010-0000-1234" not in str(event)


def test_admin_order_detail_404_is_logged_as_failure(admin_with_order, engine):
    assert admin_with_order.get("/admin/orders/777").status_code == 404
    [event] = [p for p in outbox_payloads(engine) if p["action"] == "READ"]
    assert event["result"] == "FAILURE" and event["subject"]["count"] == 0


def test_admin_order_detail_requires_login(client):
    assert client.get("/admin/orders/1").status_code == 401


def test_withdrawn_members_order_has_no_shipping_info(client, engine, password_hash):
    signup(client)
    _order(client, _add_address(client))
    shop_login(client)
    assert client.post("/shop/me/withdraw", json={"password": CUSTOMER_PASSWORD}).status_code == 204

    add_operator(engine, password_hash)
    assert login(client).status_code == 200
    body = client.get("/admin/orders/1").json()
    assert body["member_id"] is None and all(body[c] is None for c in SHIP_COLUMNS)
    [event] = [p for p in outbox_payloads(engine) if p["action"] == "READ"]
    assert event["subject"]["count"] == 0  # 개인과 끊긴 주문
