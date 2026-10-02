"""마이페이지 — 열람·정정, 비밀번호 변경, 선택 동의 철회 이력, 탈퇴 즉시 파기"""

from sqlalchemy import func, select

from app.auth.tokens import CUSTOMER
from app.models import member, member_consent

from conftest import CUSTOMER_PASSWORD, shop_login, signup

NEW_PASSWORD = "brand-new-pass-99"


def _count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def test_me_requires_login(client):
    assert client.get("/shop/me").status_code == 401


def test_me_shows_profile_and_current_consents(client):
    signup(client, phone="010-0000-1234", address="서울특별시 가상구 가상로 1")

    me = client.get("/shop/me").json()

    assert me["email"] == "buyer@example.com" and me["phone"] == "010-0000-1234"
    assert "password_hash" not in me
    consents = {c["code"]: c["agreed"] for c in me["consents"]}
    assert consents == {
        "TOS": True,
        "PRIVACY_REQUIRED": True,
        "AGE_OVER_14": True,
        "MARKETING": False,
    }


def test_shop_responses_are_not_cached(client):
    signup(client)
    assert client.get("/shop/me").headers["cache-control"] == "no-store"


def test_update_profile(client):
    signup(client)
    res = client.patch("/shop/me", json={"name": "새이름", "phone": "01099990000", "address": "  "})
    assert res.status_code == 200
    body = res.json()
    assert (body["name"], body["phone"], body["address"]) == ("새이름", "010-9999-0000", None)


def test_change_password(client):
    signup(client)
    wrong = client.post(
        "/shop/me/password",
        json={"current_password": "not-my-pass-1", "new_password": NEW_PASSWORD},
    )
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "WRONG_PASSWORD"

    ok = client.post(
        "/shop/me/password",
        json={"current_password": CUSTOMER_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert ok.status_code == 204
    assert shop_login(client).status_code == 401
    assert shop_login(client, password=NEW_PASSWORD).status_code == 200


def test_marketing_consent_withdraw_and_reagree_are_appended(client, engine):
    signup(client)

    assert client.put("/shop/me/consents/MARKETING", json={"agreed": True}).status_code == 200
    res = client.put("/shop/me/consents/MARKETING", json={"agreed": False})

    assert {c["code"]: c["agreed"] for c in res.json()["consents"]}["MARKETING"] is False
    with engine.connect() as conn:
        history = conn.execute(
            select(member_consent.c.agreed)
            .where(member_consent.c.item_code == "MARKETING")
            .order_by(member_consent.c.id)
        ).scalars()
        # 가입 시 미동의 → 동의 → 철회 — 덮어쓰지 않고 쌓인다
        assert list(history) == [False, True, False]


def test_required_consent_cannot_be_withdrawn(client):
    signup(client)
    res = client.put("/shop/me/consents/PRIVACY_REQUIRED", json={"agreed": False})
    assert res.status_code == 400 and res.json()["error"]["code"] == "REQUIRED_CONSENT"
    assert client.put("/shop/me/consents/NOPE", json={"agreed": True}).status_code == 404


def test_withdraw_requires_password(client, engine):
    signup(client)
    res = client.post("/shop/me/withdraw", json={"password": "not-my-pass-1"})
    assert res.status_code == 400
    assert _count(engine, member) == 1


def test_withdraw_destroys_member_and_consents_immediately(client, engine):
    signup(client)
    signup(client, email="other@example.com")  # 다른 회원은 그대로
    shop_login(client)

    res = client.post("/shop/me/withdraw", json={"password": CUSTOMER_PASSWORD})

    assert res.status_code == 204
    assert "max-age=0" in res.headers["set-cookie"].lower()
    with engine.connect() as conn:
        emails = conn.execute(select(member.c.email)).scalars().all()
        consent_members = conn.execute(select(member_consent.c.member_id).distinct()).scalars()
        assert emails == ["other@example.com"]  # 행 자체가 사라짐 (논리 삭제 아님)
        assert list(consent_members) == [2]
    # 남아 있던 토큰으로도 더는 들어갈 수 없다
    client.cookies.set(CUSTOMER.cookie_name, "stale")
    assert client.get("/shop/me").status_code == 401
    assert shop_login(client).status_code == 401
