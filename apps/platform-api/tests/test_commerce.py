"""주문·결제(PG 목업)·환불계좌 — 암호화, 마스킹, 결제수단 조회 기록, 탈퇴 분리보관 (①)"""

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import SecretStr
from sqlalchemy import func, insert, select

from app.config import Settings
from app.crypto import FieldCipher, refund_account_context
from app.main import create_app
from app.models import (
    destruction_history,
    member,
    orders,
    payment,
    refund_account,
    retained_member_record,
)

from conftest import (
    CUSTOMER_PASSWORD,
    TEST_PAYMENT_KEY,
    add_operator,
    login,
    outbox_payloads,
    signup,
)

ACCOUNT = "110-0000-123456"  # 가상 계좌번호
DIGITS = "1100000123456"


def _cipher() -> FieldCipher:
    return FieldCipher(bytes.fromhex(TEST_PAYMENT_KEY))


def _register_account(client, number: str = ACCOUNT):
    return client.put(
        "/shop/me/refund-account",
        json={"bank_name": "하늘은행", "account_holder": "구매자", "account_number": number},
    )


def _set_address(client):
    """회원 주소 (탈퇴 분리보관에 담기지 않는지 보려고)"""
    profile = {"name": "구매자", "phone": "010-0000-1234", "address": "서울특별시 가상구 가상로 1"}
    assert client.patch("/shop/me", json=profile).status_code == 200


def _buy(client, product_id: int = 1, card: str = "하늘카드"):
    return client.post("/shop/orders", json={"product_id": product_id, "card_company": card})


# ── 암호화 (§7②6호) ──────────────────────────────────────


def test_cipher_roundtrip_and_tamper_detection():
    cipher = _cipher()
    blob = cipher.encrypt(DIGITS, refund_account_context(1))

    assert DIGITS.encode() not in blob
    assert cipher.decrypt(blob, refund_account_context(1)) == DIGITS
    assert cipher.encrypt(DIGITS, refund_account_context(1)) != blob  # 매번 다른 nonce
    with pytest.raises(InvalidTag):  # 다른 회원 행으로 옮겨 붙이면 복호화 실패
        cipher.decrypt(blob, refund_account_context(2))
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(InvalidTag):  # 저장값 변조 탐지
        cipher.decrypt(tampered, refund_account_context(1))


@pytest.mark.parametrize("key", [None, "short", "zz" * 32, "00" * 16])
def test_api_refuses_to_start_without_valid_payment_key(settings: Settings, key):
    with pytest.raises(ValueError, match="PAYMENT_ENCRYPTION_KEY"):
        secret = SecretStr(key) if key else None
        create_app(settings.model_copy(update={"payment_encryption_key": secret}))


# ── 고객: 상품·주문·결제 ─────────────────────────────────


def test_products_are_public(client):
    body = client.get("/shop/products").json()
    assert len(body["items"]) == 6 and "하늘카드" in body["card_companies"]


def test_order_requires_login(client):
    assert _buy(client).status_code == 401


def test_order_stores_pg_result_without_card_number(client, engine):
    signup(client)
    res = client.post(
        "/shop/orders",
        # 화면이 금액·카드번호를 보내도 받지 않는다 — 금액은 상품 가격, 카드번호는 칸이 없음
        json={
            "product_id": 1,
            "card_company": "하늘카드",
            "amount": 1,
            "card_number": "4111111111111111",
        },
    )

    assert res.status_code == 201
    order = res.json()
    assert order["amount"] == 12000 and order["status"] == "PAID"
    assert order["pg_tid"].startswith("MOCKPG-") and order["card_company"] == "하늘카드"
    with engine.connect() as conn:
        row = conn.execute(select(payment)).mappings().one()
    assert "4111111111111111" not in str(dict(row))
    assert set(payment.c.keys()) == {
        "id",
        "order_id",
        "method",
        "card_company",
        "pg_tid",
        "amount",
        "approved_at",
    }


@pytest.mark.parametrize(
    "body",
    [{"product_id": 1, "card_company": "실존카드"}, {"product_id": 0, "card_company": "하늘카드"}],
)
def test_invalid_order_is_rejected(client, body):
    signup(client)
    assert client.post("/shop/orders", json=body).status_code == 400


def test_unknown_product_is_404(client):
    signup(client)
    assert _buy(client, product_id=999).status_code == 404


def test_my_orders_show_only_mine(client):
    signup(client)
    _buy(client)
    signup(client, email="other@example.com")
    _buy(client, product_id=2)

    items = client.get("/shop/orders").json()["items"]
    assert [i["product_name"] for i in items] == ["리넨 에코백"]


# ── 고객: 환불계좌 ───────────────────────────────────────


def test_refund_account_is_encrypted_and_masked(client, engine):
    signup(client)
    res = _register_account(client)

    assert res.status_code == 200
    view = res.json()["refund_account"]
    assert view["account_last4"] == "3456" and "account_number" not in view
    with engine.connect() as conn:
        row = conn.execute(select(refund_account)).mappings().one()
    assert DIGITS.encode() not in row["account_number_enc"]
    assert _cipher().decrypt(row["account_number_enc"], refund_account_context(1)) == DIGITS


def test_refund_account_replace_and_delete(client, engine):
    signup(client)
    _register_account(client)
    assert (
        _register_account(client, "220-0000-99998888").json()["refund_account"]["account_last4"]
        == "8888"
    )
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(refund_account)).scalar_one() == 1

    assert client.delete("/shop/me/refund-account").status_code == 204
    assert client.get("/shop/me/refund-account").json()["refund_account"] is None


@pytest.mark.parametrize("number", ["12345", "abcd-efgh-ijkl", "1" * 17])
def test_invalid_account_number_is_rejected(client, number):
    signup(client)
    assert _register_account(client, number).status_code == 400


def test_customer_commerce_is_not_access_logged(client, engine):
    signup(client)
    _buy(client)
    client.get("/shop/orders")
    _register_account(client)
    client.get("/shop/me/refund-account")
    assert outbox_payloads(engine) == []


# ── 관리자: 주문 목록·회원 상세·결제수단 전체 보기 ─────────


@pytest.fixture
def shop_with_customer(client, engine, password_hash):
    """고객 1명(주문 2건·환불계좌) + 관리자 로그인 상태의 client"""
    signup(client)
    _buy(client)
    _buy(client, product_id=3)
    _register_account(client)
    client.cookies.clear()
    add_operator(engine, password_hash)
    assert login(client).status_code == 200
    return client


def _events(engine, category: str) -> list[dict]:
    return [p for p in outbox_payloads(engine) if p.get("data_category") == category]


def test_admin_order_list_is_logged_as_order_read(shop_with_customer, engine):
    res = shop_with_customer.get("/admin/orders")

    assert res.status_code == 200 and res.json()["total"] == 2
    [event] = _events(engine, "ORDER")
    assert event["action"] == "READ" and event["request"]["path"] == "/admin/orders"
    # 같은 회원의 주문 2건 → 정보주체는 1명
    assert event["subject"]["ids"] == ["1"] and event["subject"]["count"] == 1


def test_member_detail_masks_refund_account(shop_with_customer, engine):
    res = shop_with_customer.get("/admin/members/1")

    assert res.status_code == 200
    body = res.json()
    assert body["refund_account"]["account_last4"] == "3456"
    assert DIGITS not in res.text and "account_number_enc" not in res.text
    assert len(body["orders"]) == 2
    [event] = _events(engine, "MEMBER_BASIC")
    assert event["request"]["path"] == "/admin/members/{member_id}"  # 경로 변수 값은 싣지 않음
    assert event["subject"]["ids"] == ["1"]
    assert _events(engine, "PAYMENT") == []  # 끝 4자리는 결제수단 조회가 아니다


def test_reveal_refund_account_is_logged_as_payment_read(shop_with_customer, engine):
    res = shop_with_customer.get("/admin/members/1/refund-account")

    assert res.status_code == 200 and res.json()["account_number"] == DIGITS
    [event] = _events(engine, "PAYMENT")
    assert event["action"] == "READ" and event["result"] == "SUCCESS"
    assert event["subject"]["ids"] == ["1"]
    assert DIGITS not in str(event)  # 접속기록에 계좌번호가 실리지 않는다 (CLAUDE.md 3절 #3)


def test_failed_lookups_still_record_who_was_targeted(shop_with_customer, engine):
    assert shop_with_customer.get("/admin/members/777").status_code == 404
    assert shop_with_customer.get("/admin/members/777/refund-account").status_code == 404

    for category in ("MEMBER_BASIC", "PAYMENT"):
        [event] = _events(engine, category)
        assert event["result"] == "FAILURE" and event["subject"]["ids"] == ["777"]


def test_admin_commerce_routes_require_login(client):
    for path in ("/admin/orders", "/admin/members/1", "/admin/members/1/refund-account"):
        assert client.get(path).status_code == 401


# ── 탈퇴: 주문 기록 분리보관 + 파기 이력 ─────────────────


def test_withdraw_retains_order_records_and_destroys_the_rest(client, engine):
    signup(client)
    _set_address(client)
    _buy(client)
    _register_account(client)

    assert client.post("/shop/me/withdraw", json={"password": CUSTOMER_PASSWORD}).status_code == 204

    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(member)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(refund_account)).scalar_one() == 0
        # 주문은 남지만 누구의 것인지 끊긴다 (FK ON DELETE SET NULL)
        assert conn.execute(select(orders.c.member_id)).scalar_one() is None
        retained = conn.execute(select(retained_member_record)).mappings().one()
        destroyed = conn.execute(select(destruction_history)).mappings().one()

    assert retained["retain_reason"] == "PAYMENT_5Y"
    assert retained["legal_basis"] == "전자상거래법 시행령 §6①3호"
    years = (retained["retain_until"] - retained["created_at"]).days / 365
    assert 4.99 < years < 5.01
    data = retained["data"]
    assert data["orders"][0]["amount"] == 12000 and data["orders"][0]["pg_tid"].startswith("MOCKPG")
    assert data["contact"] == {
        "name": "구매자",
        "email": "buyer@example.com",
        "phone": "010-0000-1234",
    }
    # 보존 목적에 필요 없는 것은 담지 않는다
    assert "가상로" not in str(data) and DIGITS not in str(data) and "argon2" not in str(data)
    assert destroyed["target_type"] == "MEMBER" and destroyed["deleted_count"] == 1
    assert "buyer@example.com" not in str(dict(destroyed))


def test_withdraw_without_orders_retains_nothing(client, engine):
    signup(client)
    client.post("/shop/me/withdraw", json={"password": CUSTOMER_PASSWORD})
    with engine.connect() as conn:
        assert (
            conn.execute(select(func.count()).select_from(retained_member_record)).scalar_one() == 0
        )


def test_seeded_style_refund_account_is_revealable(shop_with_customer, engine):
    """시드처럼 직접 넣은 암호문도 같은 키·AAD로 열린다 (시드 = 실제 저장 경로)"""
    with engine.begin() as conn:
        conn.execute(
            insert(member).values(
                id=50, email="seed@example.com", password_hash="x", name="시드회원"
            )
        )
        conn.execute(
            insert(refund_account).values(
                member_id=50,
                bank_name="숲은행",
                account_holder="시드회원",
                account_number_enc=_cipher().encrypt("000012345678", refund_account_context(50)),
                account_last4="5678",
            )
        )
    res = shop_with_customer.get("/admin/members/50/refund-account")
    assert res.json()["account_number"] == "000012345678"
