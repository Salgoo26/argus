"""회원가입 변경 — 아이디 중복 확인·이메일 인증·휴대폰 본인확인(가상)

커머스 확장, 판단기록 F1·F2
"""

import logging
from datetime import date

import pytest
from sqlalchemy import select

from app.models import member, outbox

from conftest import (
    CUSTOMER_PASSWORD,
    REQUIRED_CONSENTS,
    login_id_for,
    shop_login,
    signup,
    verify_identity,
)

RRN7 = "9001012"  # 가상 주민번호 앞 7자리 — 서버 어디에도 남으면 안 된다


def test_login_id_availability(client):
    assert client.get("/shop/auth/login-id-available?login_id=Buyer").json() == {
        "login_id": "buyer",
        "available": True,
    }
    signup(client)
    assert client.get("/shop/auth/login-id-available?login_id=buyer").json()["available"] is False
    assert client.get("/shop/auth/login-id-available?login_id=1abc").status_code == 400


def test_duplicate_login_id_is_rejected(client):
    assert signup(client).status_code == 201
    res = signup(client, email="other@example.com", login_id="buyer")
    assert res.status_code == 409 and res.json()["error"]["code"] == "LOGIN_ID_TAKEN"


@pytest.mark.parametrize("login_id", ["abc", "a" * 21, "1buyer", "buyer!", "구매자아이디"])
def test_invalid_login_id_is_rejected(client, login_id):
    assert signup(client, login_id=login_id).status_code == 400


def test_signup_stores_every_identity_result(client, engine):
    """F1 — 본인확인 결과를 모두 저장한다. 이름·휴대폰은 본인확인 결과로 정해진다"""
    res = signup(
        client,
        name="홍길동",
        phone="01000005678",
        birth_date="1990-01-01",
        gender="M",
        carrier="KT",
    )
    assert res.status_code == 201
    with engine.connect() as conn:
        row = conn.execute(select(member)).mappings().one()
    assert (row["login_id"], row["email"]) == ("buyer", "buyer@example.com")
    assert (row["name"], row["phone"]) == ("홍길동", "010-0000-5678")
    assert (row["birth_date"], row["gender"], row["carrier"]) == (date(1990, 1, 1), "M", "KT")
    assert row["is_foreigner"] is False
    assert len(row["ci"]) == 88 and len(row["di"]) == 64
    assert row["email_verified_at"] and row["identity_verified_at"]


def test_resident_number_never_reaches_the_server(client, engine, caplog):
    """가상 본인확인 창은 주민번호 앞 7자리를 창 안에서만 쓴다 — 화면이 잘못 보내도 칸이 없어
    버려진다 (개인정보 보호법 제24조의2, 결제창 카드번호와 같은 구조)"""
    caplog.set_level(logging.DEBUG)
    res = verify_identity(client, rrn7=RRN7, resident_number=RRN7)
    assert res.status_code == 200 and RRN7 not in res.text
    token = res.json()["token"]
    email = client.post("/shop/verifications/email", json={"email": "buyer@example.com"}).json()
    body = {
        "login_id": "buyer",
        "password": CUSTOMER_PASSWORD,
        "email_token": email["token"],
        "identity_token": token,
        "consents": REQUIRED_CONSENTS,
        "rrn7": RRN7,
    }
    assert client.post("/shop/auth/signup", json=body).status_code == 201
    with engine.connect() as conn:
        stored = str(dict(conn.execute(select(member)).mappings().one()))
        sent = str(conn.execute(select(outbox.c.payload)).scalars().all())
    assert RRN7 not in stored and RRN7 not in sent and RRN7 not in caplog.text
    assert RRN7 not in token  # 결과 토큰에도 없다


def test_identity_verification_requires_agency_consent(client):
    res = verify_identity(client, agreed=False)
    assert res.status_code == 400 and res.json()["error"]["code"] == "IDENTITY_CONSENT_REQUIRED"


def test_under_14_cannot_sign_up(client, engine):
    this_year = date.today().year
    res = signup(client, birth_date=f"{this_year - 10}-01-01")
    assert res.status_code == 400 and res.json()["error"]["code"] == "UNDER_14"


def test_one_account_per_person(client):
    """같은 사람(CI = 이름·생년월일·성별) — 이메일·아이디가 달라도 두 번째 가입은 거부"""
    person = {"name": "홍길동", "birth_date": "1990-01-01", "gender": "M"}
    assert signup(client, **person).status_code == 201
    res = signup(client, email="second@example.com", **person)
    assert res.status_code == 409 and res.json()["error"]["code"] == "ALREADY_REGISTERED"


def test_email_verification_reports_taken_email(client):
    signup(client)
    res = client.post("/shop/verifications/email", json={"email": "BUYER@example.com"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "EMAIL_TAKEN"


def test_tokens_cannot_be_swapped(client):
    """이메일 인증 토큰을 본인확인 자리에(반대도) 쓸 수 없다 — audience 분리"""
    email = client.post("/shop/verifications/email", json={"email": "buyer@example.com"}).json()
    body = {
        "login_id": "buyer",
        "password": CUSTOMER_PASSWORD,
        "email_token": email["token"],
        "identity_token": email["token"],
        "consents": REQUIRED_CONSENTS,
    }
    res = client.post("/shop/auth/signup", json=body)
    assert res.status_code == 400 and res.json()["error"]["code"] == "VERIFICATION_REQUIRED"


def test_login_uses_login_id_not_email(client):
    signup(client)
    client.cookies.clear()
    assert shop_login(client).status_code == 200
    by_email = client.post(
        "/shop/auth/login", json={"login_id": "buyer@example.com", "password": CUSTOMER_PASSWORD}
    )
    assert by_email.status_code == 401
    assert login_id_for("buyer@example.com") == "buyer"


def test_profile_shows_login_id_and_identity(client):
    signup(client, birth_date="1990-01-01", gender="F")
    me = client.get("/shop/me").json()
    assert me["login_id"] == "buyer" and me["birth_date"] == "1990-01-01"
    assert "ci" not in me and "di" not in me
