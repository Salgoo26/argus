"""테스트 공통 fixture — DATABASE_URL(관리자 권한)로 세션마다 임시 DB를 만들고 끝나면 지운다.

상태 진행·통지 루프는 끄고(worker_enabled=False) 테스트가 tick()을 직접 부른다.
쇼핑몰 통지는 실제로 보내지 않고 Outbox(가짜 전송기)에 모은다.
"""

import json
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
API_KEY = "test-courier-api-key-not-for-production-01"  # 테스트 전용 더미 값
WEBHOOK_SECRET = "test-courier-webhook-secret-not-for-prod-01"  # 테스트 전용 더미 값
SHOP = {
    "name": "데모샵",
    "phone": "02-0000-0000",
    "zipCode": "90000",
    "address": "서울특별시 가상구 물류로 1",
}
CUSTOMER = {
    "name": "홍길동",  # 가상
    "phone": "010-0000-1234",
    "zipCode": "90001",
    "address": "서울특별시 가상구 가상로 1",
    "addressDetail": "101호",
}


class Outbox:
    def __init__(self):
        self.sent: list[tuple[dict, bytes]] = []
        self.status: int | None = 200

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> int | None:
        self.sent.append((headers, body))
        return self.status

    def shipments(self) -> list[dict]:
        return [json.loads(body)["shipment"] for _, body in self.sent]


@pytest.fixture(scope="session")
def settings():
    if not ADMIN_URL:
        pytest.skip("DATABASE_URL 미설정 — DB 테스트 생략")
    name = f"courier_test_{secrets.token_hex(4)}"
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
            api_key=API_KEY,
            webhook_secret=WEBHOOK_SECRET,
            webhook_url="http://shop.test/partners/courier/events",
            worker_enabled=False,
        )
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture(scope="session")
def outbox():
    return Outbox()


@pytest.fixture(scope="session")
def app(settings, outbox):
    application = create_app(settings, send=outbox)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app, outbox):
    with app.state.engine.begin() as conn:
        conn.execute(text("TRUNCATE shipment, shipment_event"))
        conn.execute(text("UPDATE setting SET value = 'on' WHERE key = 'auto_progress'"))
    outbox.sent.clear()
    outbox.status = 200
    with TestClient(app) as c:
        yield c


def headers(key: str = API_KEY) -> dict:
    return {"Authorization": f"Bearer {key}"}


def dispatch(client, order_no: str = "ORDER-00000001", **overrides):
    body = {
        "orderNo": order_no,
        "sender": SHOP,
        "receiver": CUSTOMER,
        "memo": "문 앞에 놓아 주세요",
        "items": [{"name": "머그컵", "quantity": 1}],
        **overrides,
    }
    return client.post("/v1/shipments", json=body, headers=headers())


def age(app, shipment_id: str, seconds: int) -> None:
    """현재 상태에 머문 시간을 늘린다 — 실제로 기다리지 않고 시간 경과를 흉내"""
    with app.state.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE shipment SET status_changed_at = now() - make_interval(secs => :s)"
                " WHERE shipment_id = :id"
            ),
            {"s": seconds, "id": shipment_id},
        )
