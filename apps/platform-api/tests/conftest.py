"""테스트 공통 fixture.

DATABASE_URL(관리자 권한: CI는 서비스 컨테이너의 ci, 로컬은 platform-db 소유자)로
세션마다 임시 DB를 만들고 → 실제 마이그레이션을 적용하고 → 끝나면 지운다 (argus-api와 같은 방식).
"""

import hashlib
import hmac
import json
import os
import secrets
import time
import zlib
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import URL, make_url

from app.auth.passwords import hash_password
from app.config import Settings
from app.main import create_app
from app.models import operator, outbox
from app.partners import Partners
from app.partners.courier import CourierError
from app.partners.http import PartnerUnavailable
from app.partners.pg import PgError

APP_ROOT = Path(__file__).resolve().parent.parent
ADMIN_URL = os.environ.get("DATABASE_URL")
TEST_AUTH_SECRET = "test-auth-secret-not-for-production-0123456789"  # 테스트 전용 더미 값
TEST_PASSWORD = "test-password-1234"  # 테스트 전용 더미 값
TEST_PAYMENT_KEY = "00" * 32  # 테스트 전용 더미 AES 키 (16진수 64자)
TEST_DB_GATEWAY_TOKEN_KEY = "test-db-gateway-key-not-for-production-01"  # 테스트 전용 더미 값
# 가상 PG·택배 — 테스트는 실제 서비스 대신 아래 FakePg·FakeCourier를 쓴다 (테스트 전용 더미 값)
TEST_PG_SECRET = "test-pg-secret-key-not-for-production-0123"
TEST_COURIER_API_KEY = "test-courier-api-key-not-for-production-01"
TEST_COURIER_WEBHOOK_SECRET = "test-courier-webhook-secret-not-for-prod-1"
# 테스트 클라이언트의 접속 주소 — 기본값 "testclient"는 IP가 아니라 접속지로 기록할 수 없다.
# 203.0.113.0/24는 문서·예시용으로 예약된 대역 (RFC 5737)
TEST_CLIENT_ADDR = ("203.0.113.10", 50000)

requires_db = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL 미설정 — DB 테스트 생략")


def create_database(prefix: str) -> tuple[str, URL]:
    name = f"{prefix}_{secrets.token_hex(4)}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(ADMIN_URL).set(drivername="postgresql+psycopg", database=name)
    return name, url


def drop_database(name: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def alembic_config(url: URL) -> Config:
    cfg = Config(str(APP_ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


@pytest.fixture(scope="session")
def db_url():
    if not ADMIN_URL:
        pytest.skip("DATABASE_URL 미설정 — DB 테스트 생략")
    name, url = create_database("platform_test")
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        drop_database(name)


@pytest.fixture(scope="session")
def settings(db_url: URL) -> Settings:
    return Settings(
        db_host=db_url.host,
        db_port=db_url.port or 5432,
        db_name=db_url.database,
        db_user=db_url.username,
        db_password=db_url.password,
        auth_secret=TEST_AUTH_SECRET,
        payment_encryption_key=TEST_PAYMENT_KEY,
        db_gateway_token_key=TEST_DB_GATEWAY_TOKEN_KEY,
        pg_client_key="test_ck_platform",
        pg_secret_key=TEST_PG_SECRET,
        courier_api_key=TEST_COURIER_API_KEY,
        courier_webhook_secret=TEST_COURIER_WEBHOOK_SECRET,
        # TestClient는 http://testserver — Secure 쿠키면 다음 요청에 실리지 않는다
        cookie_secure=False,
    )


@pytest.fixture(scope="session")
def app(settings: Settings):
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture(scope="session")
def engine(db_url: URL):
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


class FakePg:
    """가상 PG 대역 — 결제창(브라우저 단계)은 approve()로 흉내,
    승인·조회·취소는 실제 API와 같은 모양의 응답"""

    checkout_url = "http://pg.test/checkout"
    client_key = "test_ck_platform"

    def __init__(self):
        self.payments: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.down = False

    def _check(self):
        if self.down:
            raise PartnerUnavailable("pg down")

    def approve(self, fields: dict, card_company: str = "하늘카드") -> str:
        """고객이 PG 결제창에서 카드 정보를 입력한 상태 → paymentKey"""
        key = "pay_" + secrets.token_hex(8)
        self.payments[key] = {
            "paymentKey": key,
            "orderId": fields["orderId"],
            "totalAmount": int(fields["amount"]),
            "status": "IN_PROGRESS",
            "fields": dict(fields),
            "card": {
                "company": card_company,
                "number": "123456******3452",
                "installmentPlanMonths": 0,
                "approveNo": "12345678",
            },
            "cancels": [],
        }
        return key

    def confirm(self, payment_key: str, order_id: str, amount: int) -> dict:
        self._check()
        self.calls.append(("confirm", payment_key, order_id, amount))
        p = self.payments.get(payment_key)
        if p is None:
            raise PgError(404, "NOT_FOUND_PAYMENT", "")
        if p["orderId"] != order_id or p["totalAmount"] != amount:
            raise PgError(400, "INVALID_REQUEST", "")
        p["status"] = "DONE"
        p["approvedAt"] = "2026-10-21T10:00:00+09:00"
        p["receipt"] = {"url": f"http://pg.test/receipt/{payment_key}"}
        return dict(p)

    def get(self, payment_key: str) -> dict:
        self._check()
        return dict(self.payments[payment_key])

    def cancel(self, payment_key: str, reason: str) -> dict:
        self._check()
        self.calls.append(("cancel", payment_key, reason))
        p = self.payments[payment_key]
        if p["status"] != "CANCELED":
            p["status"] = "CANCELED"
            p["cancels"] = [
                {"transactionKey": "ctx_" + secrets.token_hex(6), "cancelAmount": p["totalAmount"]}
            ]
        return dict(p)


class FakeCourier:
    """가상 택배 대역 — 접수 내용을 모아 두고, set_status()로 택배사 쪽 상태를 바꾼다"""

    def __init__(self):
        self.shipments: dict[str, dict] = {}
        self.payloads: dict[str, dict] = {}
        self.down = False

    def _check(self):
        if self.down:
            raise PartnerUnavailable("courier down")

    def _create(self, kind: str, payload: dict) -> dict:
        self._check()
        sid = ("SHP" if kind == "DELIVERY" else "RTN") + secrets.token_hex(4).upper()
        self.payloads[sid] = payload
        self.shipments[sid] = {
            "shipmentId": sid,
            "kind": kind,
            "orderNo": payload["orderNo"],
            "courier": "데모택배(가상)",
            "status": "RECEIVED" if kind == "DELIVERY" else "RETURN_RECEIVED",
            "trackingNo": None,
            "pickedUpAt": None,
            "deliveredAt": None,
        }
        return dict(self.shipments[sid])

    def create_shipment(self, payload: dict) -> dict:
        return self._create("DELIVERY", payload)

    def create_return(self, payload: dict) -> dict:
        return self._create("RETURN", payload)

    def get(self, shipment_id: str) -> dict:
        self._check()
        return dict(self.shipments[shipment_id])

    def cancel(self, shipment_id: str) -> dict:
        self._check()
        shipment = self.shipments[shipment_id]
        if shipment["status"] not in ("RECEIVED", "RETURN_RECEIVED", "CANCELED"):
            raise CourierError(409, "ALREADY_PICKED_UP", "")
        shipment["status"] = "CANCELED"
        return dict(shipment)

    def set_status(self, shipment_id: str, status: str) -> dict:
        shipment = self.shipments[shipment_id]
        shipment["status"] = status
        if status not in ("RECEIVED", "RETURN_RECEIVED", "CANCELED"):
            shipment["trackingNo"] = shipment["trackingNo"] or "6" + secrets.token_hex(5)
            shipment["pickedUpAt"] = shipment["pickedUpAt"] or "2026-10-21T10:00:00+09:00"
        if status in ("DELIVERED", "RETURN_DELIVERED"):
            shipment["deliveredAt"] = "2026-10-21T12:00:00+09:00"
        return dict(shipment)


@pytest.fixture(autouse=True)
def partners(request):
    """테스트마다 새 가상 PG·택배 대역 (앱을 쓰는 테스트만)"""
    if "app" not in request.fixturenames and "client" not in request.fixturenames:
        yield None
        return
    application = request.getfixturevalue("app")
    fake = Partners(pg=FakePg(), courier=FakeCourier())
    original = application.state.partners
    application.state.partners = fake
    yield fake
    application.state.partners = original


def courier_notify(client, shipment: dict, secret: str = TEST_COURIER_WEBHOOK_SECRET, **kw):
    """택배사가 보내는 상태 통지 (서명 포함)"""
    body = json.dumps({"eventId": "evt", "shipment": shipment}).encode()
    ts = str(kw.get("timestamp", int(time.time())))
    sig = "v1=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return client.post(
        "/partners/courier/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Courier-Timestamp": ts,
            "X-Courier-Signature": sig,
        },
    )


def place_order(client, partners, address_id: int, product_id: int = 1, **extra) -> dict:
    """주문 → (PG 결제창에서 카드 입력) → 승인까지. 결제완료된 주문"""
    res = client.post(
        "/shop/orders",
        json={"product_id": product_id, "shipping_address_id": address_id, **extra},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    fields = body["payment_request"]["fields"]
    key = partners.pg.approve(fields)
    res = client.post(
        f"/shop/orders/{body['order']['id']}/payment",
        json={"payment_key": key, "amount": int(fields["amount"])},
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def client(app):
    with TestClient(app, client=TEST_CLIENT_ADDR) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_tables(request):
    """테스트마다 업무 테이블을 비운다 (DB를 쓰는 테스트만)"""
    if "engine" not in request.fixturenames and "client" not in request.fixturenames:
        yield
        return
    eng = request.getfixturevalue("engine")
    with eng.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE operator, member, member_consent, outbox, orders, payment, order_claim,"
                " order_status_history, bank_deposit,"
                " refund_account, shipping_address, inquiry, db_access_token,"
                " retained_member_record, destruction_history, operator_permission_history"
                " RESTART IDENTITY"
            )
        )
    yield


@pytest.fixture(scope="session")
def password_hash() -> str:
    # argon2는 일부러 느리다 — 테스트 세션에서 한 번만 계산
    return hash_password(TEST_PASSWORD)


def add_operator(engine, password_hash: str, **overrides) -> int:
    values = {
        "login_id": "ops_park",
        "password_hash": password_hash,
        "name": "박지훈",  # 가상 인물
        "team": "OPS",
        "role": "OPS",
    }
    values.update(overrides)
    with engine.begin() as conn:
        return conn.execute(insert(operator).values(**values).returning(operator.c.id)).scalar_one()


def login(client, login_id: str = "ops_park", password: str = TEST_PASSWORD):
    return client.post("/admin/auth/login", json={"login_id": login_id, "password": password})


def outbox_payloads(engine, topic: str = "ACCESS_LOG") -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(outbox.c.payload).where(outbox.c.topic == topic).order_by(outbox.c.id)
        ).scalars()
        return list(rows)


CUSTOMER_PASSWORD = "customer-pass-1234"  # 테스트 전용 더미 값
REQUIRED_CONSENTS = {"TOS": True, "PRIVACY_REQUIRED": True, "AGE_OVER_14": True}


IDENTITY_FIELDS = ("name", "phone", "birth_date", "gender", "is_foreigner", "carrier", "agreed")


def login_id_for(email: str) -> str:
    """테스트 회원의 아이디 — 이메일 앞부분에서 (buyer@example.com → buyer)"""
    local = "".join(c for c in email.strip().lower().split("@")[0] if c.isascii() and c.isalnum())
    if not local or not local[0].isalpha():
        local = "u" + local
    return local.ljust(4, "0")[:20]


def _birth_for(email: str) -> str:
    """이메일마다 다른 생년월일 — 같은 사람(CI)으로 겹쳐 1인 1계정에 걸리지 않게"""
    offset = zlib.crc32(email.strip().lower().encode()) % 9000
    return (date(1970, 1, 1) + timedelta(days=offset)).isoformat()


def verify_identity(client, **overrides):
    body = {
        "carrier": "SKT",
        "name": "구매자",  # 가상
        "birth_date": "1990-01-01",
        "gender": "F",
        "is_foreigner": False,
        "phone": "010-0000-1234",  # 가상
        "agreed": True,
    }
    body.update(overrides)
    return client.post("/shop/verifications/identity", json=body)


def signup(client, email: str = "buyer@example.com", **overrides):
    """이메일 인증 → 휴대폰 본인확인(가상) → 가입. 인증 단계가 실패하면 그 응답을 돌려준다"""
    res = client.post("/shop/verifications/email", json={"email": email})
    if res.status_code != 200:
        return res
    email_token = res.json()["token"]
    identity = {k: overrides.pop(k) for k in IDENTITY_FIELDS if k in overrides}
    identity.setdefault("birth_date", _birth_for(email))
    res = verify_identity(client, **identity)
    if res.status_code != 200:
        return res
    body = {
        "login_id": login_id_for(email),
        "password": CUSTOMER_PASSWORD,
        "email_token": email_token,
        "identity_token": res.json()["token"],
        "consents": {**REQUIRED_CONSENTS, "MARKETING": False},
    }
    body.update(overrides)
    return client.post("/shop/auth/signup", json=body)


def finish_orders(engine) -> None:
    """모든 주문을 배송완료로 — 진행 중 주문이 있으면 탈퇴할 수 없다 (커머스 확장 0011)"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE orders SET status = 'DELIVERED', courier = '데모택배(가상)',"
                " tracking_no = 'DEMO0000000001', shipped_at = now(), delivered_at = now()"
            )
        )


def shop_login(client, email: str = "buyer@example.com", password: str = CUSTOMER_PASSWORD):
    """테스트 회원은 이메일로 부른다 — 아이디는 login_id_for(email)"""
    return client.post(
        "/shop/auth/login", json={"login_id": login_id_for(email), "password": password}
    )
