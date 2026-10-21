"""테스트 공통 fixture.

DATABASE_URL(관리자 권한: CI는 서비스 컨테이너의 ci, 로컬은 platform-db 소유자)로
세션마다 임시 DB를 만들고 → 실제 마이그레이션을 적용하고 → 끝나면 지운다 (argus-api와 같은 방식).
"""

import os
import secrets
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

APP_ROOT = Path(__file__).resolve().parent.parent
ADMIN_URL = os.environ.get("DATABASE_URL")
TEST_AUTH_SECRET = "test-auth-secret-not-for-production-0123456789"  # 테스트 전용 더미 값
TEST_PASSWORD = "test-password-1234"  # 테스트 전용 더미 값
TEST_PAYMENT_KEY = "00" * 32  # 테스트 전용 더미 AES 키 (16진수 64자)
TEST_DB_GATEWAY_TOKEN_KEY = "test-db-gateway-key-not-for-production-01"  # 테스트 전용 더미 값
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
                " order_status_history,"
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
