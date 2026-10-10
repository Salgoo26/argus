"""관리자 검색 — 회원·주문·1:1 문의 (v0.1 보강 E, 안내서 129쪽 누락 사례 ②)

검색 조건은 URL이 아니라 POST 본문으로 받는다(이름·이메일·연락처가 서버 접근 로그·브라우저 기록에
남지 않게). 접속기록에는 조건의 **키 이름만**, 정보주체는 결과로 화면에 보인 회원 PK 전부.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from app.models import inquiry, member, orders

from conftest import add_operator, login, outbox_payloads

KST_0915 = datetime(2026, 9, 15, tzinfo=UTC) - timedelta(hours=9)  # 2026-09-15 00:00 KST


def _add_members(engine, count: int) -> list[int]:
    rows = [
        {
            "email": f"user{n:04d}@example.com",
            "password_hash": "unusable",
            "name": "김가상" if n % 2 else f"이가상{n}",  # 가상
            "phone": f"010-1234-{n:04d}",
            "created_at": KST_0915 + timedelta(days=n),
        }
        for n in range(1, count + 1)
    ]
    with engine.begin() as conn:
        return [r.id for r in conn.execute(insert(member).returning(member.c.id), rows)]


@pytest.fixture
def admin(client, engine, password_hash):
    add_operator(engine, password_hash)
    assert login(client).status_code == 200
    return client


def _search(client, what: str, **body):
    res = client.post(f"/admin/{what}/search", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _last_read(engine, path: str) -> dict:
    return [p for p in outbox_payloads(engine) if p["request"]["path"] == path][-1]


# ── 회원 ─────────────────────────────────────────────────


def test_member_search_by_name_email_phone_status_and_join_date(admin, engine):
    ids = _add_members(engine, 6)  # 9/16~9/21 가입, 홀수 번째가 "김가상"

    assert [m["id"] for m in _search(admin, "members", name="김가")["items"]] == ids[0::2]
    assert [m["id"] for m in _search(admin, "members", email="user0002")["items"]] == [ids[1]]
    # 연락처는 하이픈 없이 적어도 찾는다
    assert [m["id"] for m in _search(admin, "members", phone="12340003")["items"]] == [ids[2]]
    body = _search(admin, "members", joined_from="2026-09-17", joined_to="2026-09-18")
    assert [m["id"] for m in body["items"]] == ids[1:3] and body["total"] == 2
    assert _search(admin, "members", status="WITHDRAWN")["items"] == []


def test_member_search_records_every_shown_member_and_key_names_only(admin, engine):
    ids = _add_members(engine, 30)

    body = _search(admin, "members", name="김가상", size=10)

    event = _last_read(engine, "/admin/members/search")
    assert event["action"] == "READ" and event["data_category"] == "MEMBER_BASIC"
    # 화면에 보인 회원 PK 전부 = 처리한 정보주체 (A4 — 검색 조건으로 다량 열람해도 공란이 아니다)
    assert event["subject"]["ids"] == [str(m["id"]) for m in body["items"]]
    assert event["subject"]["count"] == 10 and set(event["subject"]["ids"]) <= set(map(str, ids))
    assert event["request"]["query_keys"] == ["name", "size"]  # 키 이름만
    raw = json.dumps(event, ensure_ascii=False)
    assert "김가상" not in raw  # 검색어 값은 기록 어디에도 없다


def test_empty_result_is_still_recorded(admin, engine):
    _add_members(engine, 3)
    assert _search(admin, "members", email="nobody-here")["items"] == []
    event = _last_read(engine, "/admin/members/search")
    assert event["result"] == "SUCCESS"
    assert event["subject"] == {"type": "MEMBER", "ids": [], "count": 0, "truncated": False}
    assert event["request"]["query_keys"] == ["email"]  # 무엇을 찾으려 했는지는 키 이름으로


def test_like_wildcards_are_literal(admin, engine):
    _add_members(engine, 3)
    assert _search(admin, "members", name="%")["items"] == []
    assert _search(admin, "members", email="_")["items"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"status": "DELETED"},
        {"joined_from": "2026-09-20", "joined_to": "2026-09-01"},
        {"name": ""},
        {"phone": "010-abc"},
        {"size": 101},
    ],
)
def test_invalid_member_search_is_400(admin, body):
    assert admin.post("/admin/members/search", json=body).status_code == 400


def test_search_requires_login(client, engine):
    assert client.post("/admin/members/search", json={}).status_code == 401
    assert outbox_payloads(engine) == []


# ── 주문 ─────────────────────────────────────────────────


def _add_orders(engine, member_ids: list[int]) -> list[int]:
    rows = [
        {
            "member_id": member_id,
            "product_id": 1,
            "amount": 10000,
            "status": "PAID",
            "ordered_at": KST_0915 + timedelta(days=n),
        }
        for n, member_id in enumerate(member_ids)
    ]
    with engine.begin() as conn:
        return [r.id for r in conn.execute(insert(orders).returning(orders.c.id), rows)]


def test_order_search_by_number_member_status_and_date(admin, engine):
    a, b = _add_members(engine, 2)
    order_ids = _add_orders(engine, [a, b, a])  # 9/15, 9/16, 9/17

    assert [o["id"] for o in _search(admin, "orders", order_id=order_ids[1])["items"]] == [
        order_ids[1]
    ]
    assert {o["id"] for o in _search(admin, "orders", member_id=a)["items"]} == {
        order_ids[0],
        order_ids[2],
    }
    dated = _search(admin, "orders", ordered_from="2026-09-16", ordered_to="2026-09-16")
    assert [o["id"] for o in dated["items"]] == [order_ids[1]]
    assert len(_search(admin, "orders", status="PAID")["items"]) == 3

    event = _last_read(engine, "/admin/orders/search")
    assert event["data_category"] == "ORDER" and event["request"]["query_keys"] == ["status"]
    assert sorted(event["subject"]["ids"]) == sorted([str(a), str(b)])  # 회원은 한 번만


# ── 1:1 문의 ─────────────────────────────────────────────


def _add_inquiries(engine, items: list[tuple[int, str, str]]) -> list[int]:
    rows = [
        {
            "member_id": member_id,
            "title": title,
            "body": "가상 문의 본문",
            "status": status,
            "created_at": KST_0915 + timedelta(days=n),
        }
        for n, (member_id, title, status) in enumerate(items)
    ]
    with engine.begin() as conn:
        return [r.id for r in conn.execute(insert(inquiry).returning(inquiry.c.id), rows)]


def test_inquiry_search_by_status_member_date_and_title(admin, engine):
    a, b = _add_members(engine, 2)
    q = _add_inquiries(
        engine, [(a, "배송 문의", "OPEN"), (b, "환불 요청", "ANSWERED"), (a, "배송지 변경", "OPEN")]
    )

    assert {i["id"] for i in _search(admin, "inquiries", title="배송")["items"]} == {q[0], q[2]}
    assert [i["id"] for i in _search(admin, "inquiries", status="ANSWERED")["items"]] == [q[1]]
    assert {i["id"] for i in _search(admin, "inquiries", member_id=a)["items"]} == {q[0], q[2]}
    dated = _search(admin, "inquiries", created_from="2026-09-17", created_to="2026-09-17")
    assert [i["id"] for i in dated["items"]] == [q[2]]
    # 목록에는 본문을 싣지 않는다 (기존 목록과 같은 최소 노출)
    assert all("body" not in i for i in dated["items"])

    event = _last_read(engine, "/admin/inquiries/search")
    assert event["data_category"] == "INQUIRY" and event["subject"]["ids"] == [str(a)]
    assert event["request"]["query_keys"] == ["created_from", "created_to"]
