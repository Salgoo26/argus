"""결제·주문 E2E (기능 레이어 7-4 ③) — 로그인·배송지 필수, 카드번호 미수신, 관리자 주문 상세 기록

고객: 비로그인 주문 거부 → 가입 → 배송지 없이 주문 거부 → 배송지 등록 → 주문.
가상 결제창은 카드번호를 브라우저에서만 확인하고 보내지 않는다 — 여기서는 **잘못 만든 화면이
카드번호를 실어 보낸 상황**을 흉내 내, 그래도 서버가 받지 않는지 본다.
관리자(ops_park): 주문 상세(배송 정보) → READ·ORDER 접속기록이 Argus 원장에 도착.
카드번호는 응답·Argus 원장 어디에도 없어야 한다.
서버 로그는 scripts/e2e.sh가 컨테이너 로그를 훑어 확인한다.
"""

import os
import secrets

import psycopg

from conftest import PLATFORM_URL, Browser, platform_login, wait_until

PASSWORD = "e2e-buyer-pass-4"  # noqa: S105 — 테스트 전용 더미 값, 매번 새 가상 계정
# 가상 카드번호 — Luhn 검사만 통과하는 실존하지 않는 번호. scripts/e2e.sh의 로그 검사와 같은 값
CARD = "1234567890123452"
CARD_FORMS = (CARD, "1234-5678-9012-3452")
STREET = "가상시 가상구 결제로 4"


def _ledger(query: str, params: tuple) -> list[tuple]:
    """운영자 확인 — Argus 앱 계정으로 원장 조회 (SELECT만 가능한 계정)"""
    conninfo = psycopg.conninfo.make_conninfo(
        host=os.environ["ARGUS_DB_HOST"],
        dbname=os.environ["ARGUS_DB_NAME"],
        user=os.environ["ARGUS_DB_USER"],
        password=os.environ["ARGUS_DB_PASSWORD"],
    )
    with psycopg.connect(conninfo) as conn:
        return conn.execute(query, params).fetchall()


def test_checkout_requires_login_and_address_and_never_takes_card_number():
    shop = Browser(PLATFORM_URL, "customer_session")
    order = {"product_id": 2, "card_company": "바다카드"}

    # 비로그인 주문은 거부 (화면은 로그인 화면으로 보낸 뒤 이 상품으로 돌아온다)
    assert (
        shop.post("/api/shop/orders", json={**order, "shipping_address_id": 1}).status_code == 401
    )

    email = f"e2e-{secrets.token_hex(4)}@example.com"
    joined = shop.post(
        "/api/shop/auth/signup",
        json={
            "email": email,
            "password": PASSWORD,
            "name": "가상결제자",
            "phone": "010-0000-0004",
            "consents": {"TOS": True, "PRIVACY_REQUIRED": True, "AGE_OVER_14": True},
        },
    )
    assert joined.status_code == 201, joined.text
    member_id = shop.get("/api/shop/me").json()["id"]

    # 배송지 없이는 주문할 수 없다
    assert shop.post("/api/shop/orders", json=order).status_code == 400

    home = {
        "label": "집",
        "recipient": "가상수령인",
        "phone": "010-0000-0004",
        "zip_code": "90004",
        "address": STREET,
        "address_detail": "4층",
    }
    address_id = shop.post("/api/shop/me/addresses", json=home).json()["items"][0]["id"]

    # 잘못 만든 화면이 카드번호·유효기간을 실어 보낸 상황 — 서버는 칸이 없어 버린다
    placed = shop.post(
        "/api/shop/orders",
        json={
            **order,
            "shipping_address_id": address_id,
            "card_number": CARD_FORMS[1],
            "card_expiry": "12/30",
        },
    )
    assert placed.status_code == 201, placed.text
    body = placed.json()
    assert body["ship_address"] == STREET and body["ship_recipient"] == "가상수령인"
    assert body["card_company"] == "바다카드" and body["pg_tid"].startswith("MOCKPG-")
    assert not any(form in placed.text for form in CARD_FORMS)
    order_id = body["id"]

    # 배송지를 고쳐도 주문의 배송 정보는 주문 시점 그대로
    shop.request("PUT", f"/api/shop/me/addresses/{address_id}", json={**home, "address": "바뀐"})
    [mine] = [o for o in shop.get("/api/shop/orders").json()["items"] if o["id"] == order_id]
    assert mine["ship_address"] == STREET

    # ── 관리자: 주문 상세(배송 정보) → READ·ORDER 기록 ─────────────
    admin = platform_login("ops_park")
    detail = admin.get(f"/api/admin/orders/{order_id}")
    assert detail.status_code == 200
    assert detail.json()["ship_address"] == STREET and detail.json()["member_id"] == member_id
    assert not any(form in detail.text for form in CARD_FORMS)

    def arrived():
        rows = _ledger(
            "SELECT action, data_category, subject_count, subject_ids, request_path, context"
            " FROM access_log WHERE actor_login_id = 'ops_park' AND access_path = 'APP'"
            " AND request_path = '/admin/orders/{order_id}' AND %s = ANY(subject_ids)"
            " ORDER BY id DESC",
            (str(member_id),),
        )
        return rows or None

    [record, *_] = wait_until("주문 상세 조회 기록 원장 도착", arrived)
    assert record[0] == "READ" and record[1] == "ORDER" and record[2] == 1
    # 원장에는 회원 PK만 — 배송지 주소·연락처·카드번호는 Argus로 가지 않는다 (CLAUDE.md 3절 #3)
    ledger_text = str(_ledger("SELECT * FROM access_log ORDER BY id DESC LIMIT 200", ()))
    assert STREET not in ledger_text and "010-0000-0004" not in ledger_text
    assert not any(form in ledger_text for form in CARD_FORMS)
