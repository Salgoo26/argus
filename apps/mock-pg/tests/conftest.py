"""테스트 공통 fixture — DATABASE_URL(관리자 권한)로 세션마다 임시 DB를 만들고 끝나면 지운다"""

import base64
import os
import secrets

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.main import create_app

ADMIN_URL = os.environ.get("DATABASE_URL")
CLIENT_KEY = "test_ck_mockpg"
SECRET_KEY = "test-secret-key-not-for-production-0123456789"  # 테스트 전용 더미 값
SHOP = "http://shop.test"
CARD = "1234567890123452"  # 가상 카드번호 (실존하지 않는 번호)


@pytest.fixture(scope="session")
def settings():
    if not ADMIN_URL:
        pytest.skip("DATABASE_URL 미설정 — DB 테스트 생략")
    name = f"mockpg_test_{secrets.token_hex(4)}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(ADMIN_URL)
    try:
        yield Settings(
            db_host=url.host,
            db_port=url.port or 5432,
            db_name=name,
            db_user=url.username,
            db_password=url.password,
            client_key=CLIENT_KEY,
            secret_key=SECRET_KEY,
            allowed_return_origins=SHOP,
            public_url="http://pg.test",
        )
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture(scope="session")
def app(settings):
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    with app.state.engine.begin() as conn:
        conn.execute(text("TRUNCATE payment"))
    with TestClient(app, follow_redirects=False) as c:
        yield c


def auth(secret: str = SECRET_KEY) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{secret}:".encode()).decode()}


def open_checkout(client, **overrides):
    form = {
        "clientKey": CLIENT_KEY,
        "orderId": "ORDER-00000001",
        "orderName": "머그컵",
        "amount": "15000",
        "customerName": "홍길동",  # 가상
        "customerEmail": "buyer@example.com",
        "customerMobilePhone": "01000001234",
        "successUrl": f"{SHOP}/checkout/result?step=ok",
        "failUrl": f"{SHOP}/checkout/result?step=fail",
        **overrides,
    }
    return client.post("/checkout", data=form)


def payment_key_from(html: str) -> str:
    start = html.index("/checkout/pay_") + len("/checkout/")
    return html[start : html.index("/", start)]


def pay(client, card: str = CARD, **overrides) -> str:
    """결제창 열기 → 카드 입력. 성공 주소로 돌아갈 때의 paymentKey"""
    res = open_checkout(client, **overrides)
    assert res.status_code == 200, res.text
    key = payment_key_from(res.text)
    res = client.post(
        f"/checkout/{key}/approve",
        data={
            "card_company": "하늘카드",
            "card_number": card,
            "expiry": "12/30",
            "installment": "3",
        },
    )
    assert res.status_code == 303, res.text
    return key
