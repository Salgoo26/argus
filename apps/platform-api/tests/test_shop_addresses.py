"""배송지 관리 (기능 레이어 7-4 ②) — 기본 배송지 1개, 상한, 본인 것만, 접속기록 미대상, 이관"""

import pytest
from alembic import command
from sqlalchemy import create_engine, func, inspect, select, text

from app.models import member, shipping_address
from app.shop.addresses import MAX_ADDRESSES

from conftest import (
    alembic_config,
    create_database,
    drop_database,
    outbox_payloads,
    requires_db,
    shop_login,
    signup,
)

HOME = {
    "label": "집",
    "recipient": "구매자",  # 가상
    "phone": "01000001234",
    "zip_code": "90001",
    "address": "서울특별시 가상구 가상로 1",
    "address_detail": "101동 1001호",
}
OFFICE = {**HOME, "label": "회사", "zip_code": "90002", "address": "서울특별시 가상구 회사로 2"}


def _add(client, **overrides):
    return client.post("/shop/me/addresses", json={**HOME, **overrides})


def _defaults(items: list[dict]) -> list[str]:
    return [i["label"] for i in items if i["is_default"]]


def test_addresses_require_login(client):
    assert client.get("/shop/me/addresses").status_code == 401
    assert client.post("/shop/me/addresses", json=HOME).status_code == 401


def test_first_address_becomes_default(client):
    signup(client)
    res = _add(client)
    assert res.status_code == 201
    [item] = res.json()["items"]
    assert item["is_default"] is True
    assert item["phone"] == "010-0000-1234"  # 휴대폰과 같은 형식으로 저장
    assert item["address_detail"] == "101동 1001호"


def test_only_one_default_per_member(client):
    signup(client)
    _add(client)
    items = client.post("/shop/me/addresses", json=OFFICE).json()["items"]
    assert _defaults(items) == ["집"]  # 두 번째는 기본이 아니다

    office_id = next(i["id"] for i in items if i["label"] == "회사")
    items = client.post(f"/shop/me/addresses/{office_id}/default").json()["items"]
    assert _defaults(items) == ["회사"] and items[0]["label"] == "회사"  # 기본이 맨 앞

    # 새 배송지를 기본으로 등록해도 기본은 하나
    items = _add(client, label="부모님 댁", is_default=True).json()["items"]
    assert _defaults(items) == ["부모님 댁"]


def test_update_address(client):
    signup(client)
    [item] = _add(client).json()["items"]
    res = client.put(
        f"/shop/me/addresses/{item['id']}",
        json={**HOME, "recipient": "가족", "address_detail": "  "},
    )
    assert res.status_code == 200
    [updated] = res.json()["items"]
    assert (updated["recipient"], updated["address_detail"]) == ("가족", None)
    assert updated["is_default"] is True  # 수정으로 기본이 풀리지 않는다


def test_deleting_default_promotes_the_oldest_remaining(client, engine):
    signup(client)
    home_id = _add(client).json()["items"][0]["id"]
    client.post("/shop/me/addresses", json=OFFICE)
    _add(client, label="부모님 댁")

    res = client.delete(f"/shop/me/addresses/{home_id}")
    assert res.status_code == 200
    assert _defaults(res.json()["items"]) == ["회사"]
    with engine.connect() as conn:  # 실제 삭제
        ids = conn.execute(select(shipping_address.c.id)).scalars().all()
    assert home_id not in ids

    for item in client.get("/shop/me/addresses").json()["items"]:
        client.delete(f"/shop/me/addresses/{item['id']}")
    assert client.get("/shop/me/addresses").json()["items"] == []


def test_address_limit(client):
    signup(client)
    for n in range(MAX_ADDRESSES):
        assert _add(client, label=f"배송지{n}").status_code == 201
    res = _add(client, label="하나 더")
    assert res.status_code == 400 and res.json()["error"]["code"] == "ADDRESS_LIMIT"


def test_cannot_touch_another_members_address(client):
    signup(client, email="other@example.com")
    other_id = _add(client).json()["items"][0]["id"]
    signup(client)  # 이제 buyer@example.com 세션

    assert client.put(f"/shop/me/addresses/{other_id}", json=OFFICE).status_code == 404
    assert client.post(f"/shop/me/addresses/{other_id}/default").status_code == 404
    assert client.delete(f"/shop/me/addresses/{other_id}").status_code == 404
    assert client.get("/shop/me/addresses").json()["items"] == []

    shop_login(client, email="other@example.com")
    [mine] = client.get("/shop/me/addresses").json()["items"]
    assert mine["label"] == "집" and mine["is_default"] is True  # 그대로


@pytest.mark.parametrize(
    "overrides",
    [
        {"zip_code": "1234"},
        {"zip_code": "1234a"},
        {"zip_code": "١٢٣٤٥"},  # 다른 문자권 숫자
        {"phone": ""},
        {"phone": "02-123-4567"},
        {"recipient": "  "},
        {"address": "   "},
        {"label": ""},
        {"label": "가" * 31},
        {"address_detail": "가" * 101},
    ],
    ids=[
        "zip-short",
        "zip-letter",
        "zip-non-ascii",
        "no-phone",
        "landline",
        "blank-recipient",
        "blank-address",
        "no-label",
        "long-label",
        "long-detail",
    ],
)
def test_invalid_address_is_rejected(client, engine, overrides):
    signup(client)
    assert _add(client, **overrides).status_code == 400
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(shipping_address)).scalar_one() == 0


def test_customer_address_actions_are_not_access_logged(client, engine):
    """고객(정보주체) 본인 행위는 접속기록 대상이 아니다 (CLAUDE.md 3절 #4)"""
    signup(client)
    item_id = _add(client).json()["items"][0]["id"]
    client.get("/shop/me/addresses")
    client.put(f"/shop/me/addresses/{item_id}", json=OFFICE)
    client.post(f"/shop/me/addresses/{item_id}/default")
    client.delete(f"/shop/me/addresses/{item_id}")
    assert outbox_payloads(engine) == []


def test_profile_no_longer_has_address(client, engine):
    signup(client)
    assert "address" not in client.get("/shop/me").json()
    assert "address" not in {c["name"] for c in inspect(engine).get_columns("member")}


@requires_db
def test_migration_moves_member_address_to_default_shipping_address():
    """0007 — 기존 member.address는 그 회원의 기본 배송지가 되고, 회원 컬럼은 없어진다"""
    name, url = create_database("platform_mig_addr")
    try:
        cfg = alembic_config(url)
        command.upgrade(cfg, "0006")
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO member (email, password_hash, name, phone, address) VALUES"
                    " ('a@example.com', 'x', '가상일', '010-0000-0001', ' 서울특별시 가상구 1 '),"
                    " ('b@example.com', 'x', '가상이', NULL, NULL),"
                    " ('c@example.com', 'x', '가상삼', NULL, '   ')"
                )
            )
        command.upgrade(cfg, "0007")
        with engine.connect() as conn:
            rows = conn.execute(select(shipping_address)).mappings().all()
            emails = conn.execute(select(member.c.email).order_by(member.c.id)).scalars().all()
        assert emails == ["a@example.com", "b@example.com", "c@example.com"]
        [moved] = rows  # 주소가 있던 회원만
        assert moved["address"] == "서울특별시 가상구 1"
        assert (moved["recipient"], moved["phone"]) == ("가상일", "010-0000-0001")
        assert moved["is_default"] is True and moved["zip_code"] is None

        command.downgrade(cfg, "0006")  # 되돌리면 기본 배송지가 회원 주소로
        with engine.connect() as conn:
            back = conn.execute(text("SELECT email, address FROM member ORDER BY id")).all()
        assert back[0] == ("a@example.com", "서울특별시 가상구 1") and back[1][1] is None
        engine.dispose()
    finally:
        drop_database(name)
